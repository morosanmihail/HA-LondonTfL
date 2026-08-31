import json
import re
from pathlib import Path

import pytest

from custom_components.london_tfl.config_flow import _direction_text, _fetch_stations

FIXTURES = Path(__file__).parent.parent / "custom_components" / "london_tfl" / "test"


def _load(name: str) -> list:
    return json.loads((FIXTURES / name).read_text())


# Real (trimmed) TfL /line/{id}/stoppoints responses, fetched live:
#   - stoppoints_15.json / stoppoints_115.json: bus stoppoints for two routes that
#     partly overlap (e.g. Aldgate / Aldgate East are served by both; Cannon Street
#     only by 15; the 115-only pair only by 115) — exercises union + sort-by-coverage.
#   - stoppoints_victoria.json: tube stoppoints (station-level: no indicator/direction).
FIXTURE_BY_LINE = {
    "15": _load("stoppoints_15.json"),
    "115": _load("stoppoints_115.json"),
    "victoria": _load("stoppoints_victoria.json"),
}


def _make_fake_request(available_lines=None, unreachable_lines=()):
    """Build a fake network.request() that serves the fixtures above by line id."""
    available_lines = available_lines if available_lines is not None else FIXTURE_BY_LINE

    async def fake_request(url: str):
        line_id = re.search(r"/line/([^/]+)/stoppoints", url).group(1)
        if line_id in unreachable_lines:
            return None
        return json.dumps(available_lines[line_id])

    return fake_request


class TestDirectionText:
    def test_prefers_towards_over_compass_point(self) -> None:
        item = {
            "additionalProperties": [
                {"category": "Direction", "key": "CompassPoint", "value": "W"},
                {"category": "Direction", "key": "Towards", "value": "Bank"},
            ]
        }
        assert _direction_text(item) == "Bank"

    def test_falls_back_to_compass_point(self) -> None:
        item = {
            "additionalProperties": [
                {"category": "Direction", "key": "CompassPoint", "value": "W"},
            ]
        }
        assert _direction_text(item) == "W"

    def test_empty_when_no_direction_properties(self) -> None:
        item = {"additionalProperties": [{"category": "Facility", "key": "WiFi", "value": "yes"}]}
        assert _direction_text(item) == ""

    def test_empty_when_no_additional_properties_key(self) -> None:
        assert _direction_text({}) == ""


class TestFetchStationsSingleLine:
    async def test_bus_label_includes_indicator_and_towards(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "custom_components.london_tfl.config_flow.request", _make_fake_request()
        )
        stations = await _fetch_stations("bus", "15")

        aldgate = stations["490000003R"]
        assert aldgate.name == "Aldgate Station"
        assert aldgate.label == "Aldgate Station (Stop R) — towards Bank, London Bridge Or Tower Bridge"
        assert aldgate.display_name == "Aldgate Station (towards Bank, London Bridge Or Tower Bridge)"
        # single line selected: no coverage-count suffix
        assert "lines]" not in aldgate.label

    async def test_bus_uses_id_field_as_station_key(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "custom_components.london_tfl.config_flow.request", _make_fake_request()
        )
        stations = await _fetch_stations("bus", "15")
        assert set(stations) == {x["id"] for x in FIXTURE_BY_LINE["15"]}

    async def test_tube_label_has_no_indicator_or_direction_suffix(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "custom_components.london_tfl.config_flow.request", _make_fake_request()
        )
        stations = await _fetch_stations("tube", "victoria")

        blackhorse = stations["940GZZLUBLR"]
        assert blackhorse.label == "Blackhorse Road Underground Station"
        assert blackhorse.display_name == "Blackhorse Road Underground Station"

    async def test_tube_uses_stationNaptan_field_as_station_key(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "custom_components.london_tfl.config_flow.request", _make_fake_request()
        )
        stations = await _fetch_stations("tube", "victoria")
        assert set(stations) == {x["stationNaptan"] for x in FIXTURE_BY_LINE["victoria"]}


class TestFetchStationsMultiLine:
    async def test_union_includes_stations_from_all_lines(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "custom_components.london_tfl.config_flow.request", _make_fake_request()
        )
        stations = await _fetch_stations("bus", "15,115")

        expected_ids = {x["id"] for x in FIXTURE_BY_LINE["15"]} | {
            x["id"] for x in FIXTURE_BY_LINE["115"]
        }
        assert set(stations) == expected_ids

    async def test_shared_stations_sort_before_single_line_ones(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "custom_components.london_tfl.config_flow.request", _make_fake_request()
        )
        stations = await _fetch_stations("bus", "15,115")

        ids_15 = {x["id"] for x in FIXTURE_BY_LINE["15"]}
        ids_115 = {x["id"] for x in FIXTURE_BY_LINE["115"]}
        shared_ids = ids_15 & ids_115
        assert shared_ids, "fixture must contain at least one station shared by both lines"

        ordered = list(stations)
        shared_positions = [ordered.index(sid) for sid in shared_ids]
        other_positions = [ordered.index(sid) for sid in ordered if sid not in shared_ids]
        assert max(shared_positions) < min(other_positions)

    async def test_shared_station_label_shows_coverage_count(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "custom_components.london_tfl.config_flow.request", _make_fake_request()
        )
        stations = await _fetch_stations("bus", "15,115")

        shared_id = ({x["id"] for x in FIXTURE_BY_LINE["15"]} & {x["id"] for x in FIXTURE_BY_LINE["115"]}).pop()
        assert stations[shared_id].label.endswith("[2/2 lines]")

    async def test_single_line_only_station_label_shows_one_of_two(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "custom_components.london_tfl.config_flow.request", _make_fake_request()
        )
        stations = await _fetch_stations("bus", "15,115")

        only_15_id = next(
            x["id"] for x in FIXTURE_BY_LINE["15"]
            if x["id"] not in {y["id"] for y in FIXTURE_BY_LINE["115"]}
        )
        assert stations[only_15_id].label.endswith("[1/2 lines]")

    async def test_display_name_never_carries_coverage_count(self, monkeypatch) -> None:
        """display_name is persisted to storage, so it must stay stable across
        re-runs of the flow with a different line selection — no [n/m lines]."""
        monkeypatch.setattr(
            "custom_components.london_tfl.config_flow.request", _make_fake_request()
        )
        stations = await _fetch_stations("bus", "15,115")
        for option in stations.values():
            assert "lines]" not in option.display_name


class TestFetchStationsErrorHandling:
    async def test_unreachable_line_is_skipped_not_fatal(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "custom_components.london_tfl.config_flow.request",
            _make_fake_request(unreachable_lines=("115",)),
        )
        stations = await _fetch_stations("bus", "15,115")

        assert set(stations) == {x["id"] for x in FIXTURE_BY_LINE["15"]}

    async def test_all_lines_unreachable_returns_empty(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "custom_components.london_tfl.config_flow.request",
            _make_fake_request(unreachable_lines=("15", "115")),
        )
        stations = await _fetch_stations("bus", "15,115")
        assert stations == {}

    async def test_malformed_json_for_one_line_does_not_break_others(self, monkeypatch) -> None:
        async def fake_request(url: str):
            if "/line/15/" in url:
                return "not json"
            return json.dumps(FIXTURE_BY_LINE["115"])

        monkeypatch.setattr(
            "custom_components.london_tfl.config_flow.request", fake_request
        )
        stations = await _fetch_stations("bus", "15,115")
        assert set(stations) == {x["id"] for x in FIXTURE_BY_LINE["115"]}
