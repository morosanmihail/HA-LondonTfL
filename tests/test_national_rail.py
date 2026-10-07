import asyncio
import json
from pathlib import Path

import pytest

from custom_components.london_tfl import codes, config_flow, tfl_data
from custom_components.london_tfl.codes import CrsCodes, atco_to_crs, is_valid_crs
from custom_components.london_tfl.config_flow import (
    StationOption,
    _needs_nr_keys,
    _resolve_nr_stop,
)
from custom_components.london_tfl.network import (
    LDBWSDeparture,
    LDBWSError,
    parse_rail_data_board,
)
from custom_components.london_tfl.const import USE_LDBWS_URL
from custom_components.london_tfl.tfl_data import TfLData

FIXTURES = Path(__file__).parent.parent / "custom_components" / "london_tfl" / "test"

# Trimmed crs.codes /data/stations.json entries.
CRS_CODES_STATIONS = [
    {"name": "Carlisle", "crs": "CAR", "tiploc": "CARLILE"},
    {"name": "London Victoria", "crs": "VIC", "tiploc": "VICTRIA"},
    {"name": "Vauxhall", "crs": "VXH", "tiploc": "VAUXHAL"},
    {"name": "Abbotswood Jn", "crs": "XAY", "tiploc": "ABTSWDJ"},
    {"name": "Newport (South Wales)", "crs": "NWP", "tiploc": "NWPTRTG"},
    {"name": "Newport (Essex)", "crs": "NWE", "tiploc": "NWPTEX"},
    {"name": "Some Depot", "crs": "SDP", "tiploc": "SMDEPOT", "hasDepot": True},
]


@pytest.fixture
def crs_codes() -> CrsCodes:
    return CrsCodes(CRS_CODES_STATIONS)


@pytest.fixture(autouse=True)
def _reset_codes_caches(monkeypatch):
    monkeypatch.setattr(codes, "_crs_cache", {})
    monkeypatch.setattr(codes, "_letter_cache", {})
    monkeypatch.setattr(codes, "_crs_codes", None)
    monkeypatch.setattr(codes, "_crs_codes_loaded_at", 0.0)
    monkeypatch.setattr(codes, "_crs_codes_failed_at", None)
    monkeypatch.setattr(codes, "_crs_codes_lock", asyncio.Lock())


class _FakeResponse:
    def __init__(self, body):
        self.status = 200
        self._body = body

    async def json(self, content_type=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """aiohttp.ClientSession stand-in for crs.codes; counts requests."""

    def __init__(self, server):
        self._server = server

    def get(self, url, **kwargs):
        self._server.requests += 1
        return self._server.respond()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeCrsCodesServer:
    def __init__(self, body=None, error: Exception | None = None):
        self.body = body
        self.error = error
        self.requests = 0

    def respond(self):
        server = self

        class _Ctx:
            async def __aenter__(self):
                await asyncio.sleep(0.01)
                if server.error:
                    raise server.error
                return _FakeResponse(server.body)

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _serve_crs_codes(monkeypatch, server: _FakeCrsCodesServer) -> None:
    monkeypatch.setattr(codes.aiohttp, "ClientSession", lambda: _FakeSession(server))


class TestLoadCrsCodes:
    async def test_concurrent_callers_share_one_request(self, monkeypatch) -> None:
        server = _FakeCrsCodesServer(body=CRS_CODES_STATIONS)
        _serve_crs_codes(monkeypatch, server)
        results = await asyncio.gather(*(codes.load_crs_codes() for _ in range(5)))
        assert server.requests == 1
        assert all(r is results[0] and r.lookup("910GCARLILE") == "CAR" for r in results)

    async def test_failure_is_not_retried_immediately(self, monkeypatch) -> None:
        server = _FakeCrsCodesServer(error=asyncio.TimeoutError())
        _serve_crs_codes(monkeypatch, server)
        assert await asyncio.gather(*(codes.load_crs_codes() for _ in range(3))) == [None] * 3
        assert await codes.load_crs_codes() is None
        assert server.requests == 1

    async def test_retries_after_backoff(self, monkeypatch) -> None:
        server = _FakeCrsCodesServer(error=asyncio.TimeoutError())
        _serve_crs_codes(monkeypatch, server)
        assert await codes.load_crs_codes() is None
        monkeypatch.setattr(
            codes, "_crs_codes_failed_at", codes._crs_codes_failed_at - codes._CRS_CODES_RETRY_AFTER
        )
        server.error, server.body = None, CRS_CODES_STATIONS
        assert (await codes.load_crs_codes()).lookup("910GCARLILE") == "CAR"
        assert server.requests == 2

    async def test_keeps_stale_list_when_refresh_fails(self, monkeypatch) -> None:
        server = _FakeCrsCodesServer(body=CRS_CODES_STATIONS)
        _serve_crs_codes(monkeypatch, server)
        first = await codes.load_crs_codes()
        monkeypatch.setattr(codes, "_crs_codes_loaded_at", -codes._CRS_CODES_TTL)
        server.error = asyncio.TimeoutError()
        assert await codes.load_crs_codes() is first
        assert server.requests == 2

    async def test_failure_is_logged_with_error_type(self, monkeypatch, caplog) -> None:
        _serve_crs_codes(monkeypatch, _FakeCrsCodesServer(error=asyncio.TimeoutError()))
        await codes.load_crs_codes()
        assert "TimeoutError" in caplog.text


class TestCrsCodes:
    def test_tiploc_match(self, crs_codes) -> None:
        assert crs_codes.lookup("910GCARLILE") == "CAR"

    def test_name_fallback_when_tfl_uses_different_tiploc(self, crs_codes) -> None:
        # TfL uses VICTRIC; crs.codes lists London Victoria under VICTRIA.
        assert crs_codes.lookup("910GVICTRIC", "London Victoria Rail Station") == "VIC"

    def test_no_match_without_name(self, crs_codes) -> None:
        assert crs_codes.lookup("910GVICTRIC") is None

    def test_junctions_and_depots_excluded_from_name_match(self, crs_codes) -> None:
        assert crs_codes.lookup("910GNOPE", "Abbotswood Jn") is None
        assert crs_codes.lookup("910GNOPE", "Some Depot") is None

    def test_ambiguous_names_are_not_guessed(self) -> None:
        crs = CrsCodes(
            [
                {"name": "Newport", "crs": "NWP", "tiploc": "A"},
                {"name": "Newport", "crs": "NWE", "tiploc": "B"},
            ]
        )
        assert crs.lookup("910GNOPE", "Newport Rail Station") is None

    def test_valid_crs(self) -> None:
        assert is_valid_crs("vxh")
        assert not is_valid_crs("VX")
        assert not is_valid_crs("VX1")
        assert not is_valid_crs("")


class TestAtcoToCrs:
    async def test_uses_crs_codes_first(self, monkeypatch, crs_codes) -> None:
        async def fake_load():
            return crs_codes

        async def fail_letter(letter):
            raise AssertionError("railwaycodes.org.uk should not be queried")

        monkeypatch.setattr(codes, "load_crs_codes", fake_load)
        monkeypatch.setattr(codes, "_load_letter", fail_letter)
        assert await atco_to_crs(None, "910GVICTRIC", "London Victoria Rail Station") == "VIC"

    async def test_falls_back_to_railwaycodes(self, monkeypatch) -> None:
        async def fake_load():
            return None

        async def fake_letter(letter):
            return {"PADTON": "PAD"}

        monkeypatch.setattr(codes, "load_crs_codes", fake_load)
        monkeypatch.setattr(codes, "_load_letter", fake_letter)
        assert await atco_to_crs(None, "910GPADTON") == "PAD"


class TestParseRailDataBoard:
    def test_parses_live_fixture(self) -> None:
        res = json.loads((FIXTURES / "rail_data_board_vxh.json").read_text())
        deps = parse_rail_data_board(res)
        assert len(deps) == 3
        assert deps[0] == LDBWSDeparture(
            location_name="Vauxhall",
            platform="8",
            operator_code="SW",
            operator_id="south-western-railway",
            destination_name="Chessington South",
            scheduled_departure_time="14:21",
        )

    def test_empty_board(self) -> None:
        assert parse_rail_data_board({"locationName": "Vauxhall"}) == []
        assert parse_rail_data_board({"trainServices": None}) == []

    def test_missing_platform_and_destination(self) -> None:
        res = {
            "locationName": "X",
            "trainServices": [
                {"std": "10:00", "destination": [], "operator": "A", "operatorCode": "AA"},
                {
                    "std": "10:05",
                    "destination": [{"locationName": "Y"}],
                    "operator": "Some Operator",
                    "operatorCode": "so",
                },
            ],
        }
        deps = parse_rail_data_board(res)
        assert len(deps) == 1
        assert deps[0].platform == "?"
        assert deps[0].operator_code == "SO"

    def test_non_dict_raises(self) -> None:
        with pytest.raises(LDBWSError):
            parse_rail_data_board([])


def _departure(
    dest: str = "Brighton", operator_code: str = "SN", operator_id: str = "southern"
) -> LDBWSDeparture:
    return LDBWSDeparture(
        location_name="Vauxhall",
        platform="1",
        operator_code=operator_code,
        operator_id=operator_id,
        destination_name=dest,
        scheduled_departure_time="12:00",
    )


class _FakeClient:
    """Stand-in for RailDataLDBWS / LDBWS recording the CRS it was asked for."""

    def __init__(self, result=None, error: Exception | None = None):
        self.result = result or []
        self.error = error
        self.calls: list[str] = []

    async def get_departures(self, crs, *, n=10):
        self.calls.append(crs)
        if self.error:
            raise self.error
        return self.result


class _FakeHass:
    async def async_add_executor_job(self, target, *args):
        return target(*args)


def _patch_clients(monkeypatch, *, rdm: _FakeClient, legacy: _FakeClient) -> None:
    monkeypatch.setattr(tfl_data, "RailDataLDBWS", lambda **kwargs: rdm)
    monkeypatch.setattr(tfl_data, "LDBWS", lambda **kwargs: legacy)


class TestFetchNationalRail:
    async def test_prefers_rail_data_marketplace(self, monkeypatch) -> None:
        rdm = _FakeClient([_departure("From RDM")])
        legacy = _FakeClient([_departure("From legacy")])
        _patch_clients(monkeypatch, rdm=rdm, legacy=legacy)
        tfl = TfLData(
            method="national-rail", line="southern", station="910GVAUXHLM",
            rdm_api_key="k", nr_api_key="t", crs="vxh",
        )
        result = await tfl.fetch(_FakeHass())
        assert [r["destinationName"] for r in result] == ["From RDM"]
        assert rdm.calls == ["VXH"]
        assert legacy.calls == []

    async def test_falls_back_to_legacy_on_failure(self, monkeypatch) -> None:
        rdm = _FakeClient(error=LDBWSError("HTTP 500"))
        legacy = _FakeClient([_departure("From legacy")])
        _patch_clients(monkeypatch, rdm=rdm, legacy=legacy)
        tfl = TfLData(
            method="national-rail", line="southern", station="910GVAUXHLM",
            rdm_api_key="k", nr_api_key="t", crs="VXH",
        )
        result = await tfl.fetch(_FakeHass())
        assert [r["destinationName"] for r in result] == ["From legacy"]
        assert legacy.calls == ["VXH"]

    async def test_rdm_failure_without_legacy_token_reports_error(self, monkeypatch) -> None:
        rdm = _FakeClient(error=LDBWSError("HTTP 401"))
        legacy = _FakeClient()
        _patch_clients(monkeypatch, rdm=rdm, legacy=legacy)
        tfl = TfLData(
            method="national-rail", line="southern", station="910GVAUXHLM",
            rdm_api_key="k", crs="VXH",
        )
        assert await tfl.fetch(_FakeHass()) == "Rail Data API error"
        assert legacy.calls == []

    async def test_existing_legacy_only_entry_unchanged(self, monkeypatch) -> None:
        rdm = _FakeClient(error=AssertionError("must not be used"))
        legacy = _FakeClient([_departure("From legacy")])
        _patch_clients(monkeypatch, rdm=rdm, legacy=legacy)

        async def fake_atco_to_crs(hass, atco, name=""):
            return "VXH"

        monkeypatch.setattr(tfl_data, "atco_to_crs", fake_atco_to_crs)
        # No stored CRS, no Rail Data key: exactly what pre-upgrade entries look like.
        tfl = TfLData(
            method="national-rail", line="southern", station="910GVAUXHLM", nr_api_key="t",
        )
        result = await tfl.fetch(_FakeHass())
        assert [r["destinationName"] for r in result] == ["From legacy"]
        assert rdm.calls == []
        assert legacy.calls == ["VXH"]

    async def test_no_credentials(self) -> None:
        tfl = TfLData(method="national-rail", line="southern", station="910GVAUXHLM")
        assert "recreate" in await tfl.fetch(_FakeHass())

    async def test_tfl_line_id_maps_to_toc(self, monkeypatch) -> None:
        rdm = _FakeClient([_departure("Leeds", "NT", "northern"), _departure("Brighton", "SN")])
        _patch_clients(monkeypatch, rdm=rdm, legacy=_FakeClient())
        tfl = TfLData(
            method="national-rail", line="northern-rail", station="910GX",
            rdm_api_key="k", crs="LDS",
        )
        result = await tfl.fetch(_FakeHass())
        assert [r["destinationName"] for r in result] == ["Leeds"]


    async def test_multiple_lines_keep_trains_from_each(self, monkeypatch) -> None:
        rdm = _FakeClient(
            [
                _departure("Brighton", "SN"),
                _departure("Bedford", "TL", "thameslink"),
                _departure("Dover", "SE", "southeastern"),
            ]
        )
        _patch_clients(monkeypatch, rdm=rdm, legacy=_FakeClient())
        tfl = TfLData(
            method="national-rail", line="southern,thameslink", station="910GX",
            rdm_api_key="k", crs="ECR",
        )
        result = await tfl.fetch(_FakeHass())
        assert [r["destinationName"] for r in result] == ["Brighton", "Bedford"]
        assert rdm.calls == ["ECR"]


class TestNeedsNrKeys:
    @pytest.mark.parametrize(
        "lines",
        ["southern", "southern,southeastern", "southern,thameslink", "thameslink,c2c"],
    )
    def test_national_rail_selections_need_keys(self, lines) -> None:
        assert _needs_nr_keys("national-rail", lines)

    def test_thameslink_alone_uses_tfl(self) -> None:
        assert not _needs_nr_keys("national-rail", "thameslink")
        assert TfLData(method="national-rail", line="thameslink", station="x").url(
            station="x"
        ) != USE_LDBWS_URL

    def test_mixed_thameslink_selection_uses_ldbws(self) -> None:
        tfl = TfLData(method="national-rail", line="southern,thameslink", station="x")
        assert tfl.url(station="x") == USE_LDBWS_URL

    def test_other_methods_never_need_keys(self) -> None:
        assert not _needs_nr_keys("tube", "victoria,jubilee")


class TestResolveNrStop:
    async def test_requires_a_key(self) -> None:
        option = StationOption(label="", name="Vauxhall", display_name="", crs="VXH")
        _, errors = await _resolve_nr_stop(None, {}, "910GVAUXHLM", option)
        assert errors == {"base": "api_key_required"}

    async def test_uses_crs_from_station_list(self) -> None:
        option = StationOption(label="", name="Vauxhall", display_name="", crs="VXH")
        data, errors = await _resolve_nr_stop(
            None, {"rdm_api_key": " key "}, "910GVAUXHLM", option
        )
        assert errors == {}
        assert data == {"rdm_api_key": "key", "nr_api_key": None, "crs": "VXH"}

    async def test_manual_crs_overrides(self) -> None:
        option = StationOption(label="", name="Vauxhall", display_name="", crs="VXH")
        data, errors = await _resolve_nr_stop(
            None, {"nr_api_key": "t", "crs": "car"}, "910GVAUXHLM", option
        )
        assert errors == {}
        assert data["crs"] == "CAR"

    async def test_invalid_manual_crs(self) -> None:
        _, errors = await _resolve_nr_stop(
            None, {"rdm_api_key": "k", "crs": "VAUX"}, "910GVAUXHLM", None
        )
        assert errors == {"crs": "invalid_crs"}

    async def test_unresolvable_crs(self, monkeypatch) -> None:
        async def fail(hass, atco, name=""):
            raise ValueError("nope")

        monkeypatch.setattr(config_flow, "atco_to_crs", fail)
        _, errors = await _resolve_nr_stop(None, {"rdm_api_key": "k"}, "910GNOPE", None)
        assert errors == {"crs": "crs_not_found"}
