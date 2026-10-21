"""Timetables as TfL really serves them: a route menu per station, one timetable per direction.

The District fixtures were captured at Acton Town on Wednesday 21 October 2026, where the live feed only ever carries
a train or two a few minutes out, so the timetable has to fill in the rest.
"""

import json
from datetime import date, datetime, UTC
from pathlib import Path

import pytest
from freezegun import freeze_time

from custom_components.london_tfl import tfl_data
from custom_components.london_tfl.tfl_data import (
    TfLData,
    _is_bank_holiday,
    _pick_schedule,
    clear_timetable_cache,
)

FIXTURES = Path(__file__).parent.parent / "custom_components" / "london_tfl" / "test"
ACTON_TOWN = "940GZZLUACT"
# 17:17 BST, when a westbound train to Ealing Broadway is due.
NOW = "2026-10-21T16:17:00Z"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def _empty_cache():
    clear_timetable_cache()
    yield
    clear_timetable_cache()


@pytest.fixture
def tfl_api(monkeypatch):
    """Fake TfL serving the Acton Town District menu and both direction timetables. Records every URL asked for."""
    base = f"https://api.tfl.gov.uk/Line/district/Timetable/{ACTON_TOWN}"
    responses = {
        base: _fixture("timetable_district_940GZZLUACT_menu.json"),
        base + "?direction=inbound": _fixture("timetable_district_940GZZLUACT_inbound.json"),
        base + "?direction=outbound": _fixture("timetable_district_940GZZLUACT_outbound.json"),
    }
    calls = []

    async def fake_request(url):
        calls.append(url)
        return json.dumps(responses[url]) if url in responses else None

    monkeypatch.setattr(tfl_data, "request", fake_request)
    return calls


def _live(minutes_iso: str, platform: str, direction: str, destination_id: str, destination: str, vehicle="052"):
    return {
        "naptanId": ACTON_TOWN,
        "stationName": "Acton Town Underground Station",
        "lineId": "district",
        "platformName": platform,
        "direction": direction,
        "destinationNaptanId": destination_id,
        "destinationName": destination + " Underground Station",
        "towards": destination,
        "expectedArrival": minutes_iso,
        "vehicleId": vehicle,
    }


WESTBOUND_EALING = _live(
    "2026-10-21T16:16:49Z", "Westbound - Platform 1", "inbound", "940GZZLUEBY", "Ealing Broadway"
)


async def _stop(filter_platform: str, live: list, max_items: int = 6) -> TfLData:
    tfl = TfLData(method="tube", line="district", station=ACTON_TOWN)
    tfl.populate(live, filter_platform)
    tfl.sort_data(max_items)
    assert await tfl.fetch_timetable(None)
    return tfl


class TestFetching:
    @freeze_time(NOW)
    async def test_route_menu_is_followed_once_per_direction(self, tfl_api) -> None:
        tfl = TfLData(method="tube", line="district", station=ACTON_TOWN)
        assert await tfl.fetch_timetable(None)
        assert len(tfl_api) == 3  # the menu, then inbound and outbound
        assert {t["direction"] for t in tfl._timetables} == {"inbound", "outbound"}

    @freeze_time(NOW)
    async def test_timetable_is_shared_between_stops(self, tfl_api) -> None:
        westbound = TfLData(method="tube", line="district", station=ACTON_TOWN)
        eastbound = TfLData(method="tube", line="district", station=ACTON_TOWN)
        await westbound.fetch_timetable(None)
        await eastbound.fetch_timetable(None)
        assert len(tfl_api) == 3
        assert eastbound._timetables == westbound._timetables

    @freeze_time(NOW)
    async def test_force_refetches(self, tfl_api) -> None:
        tfl = TfLData(method="tube", line="district", station=ACTON_TOWN)
        await tfl.fetch_timetable(None)
        await tfl.fetch_timetable(None, force=True)
        assert len(tfl_api) == 6

    async def test_failed_fetch_waits_before_retrying(self, monkeypatch) -> None:
        calls = []

        async def no_reply(url):
            calls.append(url)
            return None

        monkeypatch.setattr(tfl_data, "request", no_reply)
        tfl = TfLData(method="tube", line="district", station=ACTON_TOWN)
        assert not await tfl.fetch_timetable(None)
        assert not await tfl.fetch_timetable(None)
        assert len(calls) == 1

    async def test_each_line_of_a_multi_line_stop_is_fetched(self, monkeypatch) -> None:
        calls = []

        async def fake_request(url):
            calls.append(url)
            return None

        monkeypatch.setattr(tfl_data, "request", fake_request)
        tfl = TfLData(method="tube", line="district,piccadilly", station=ACTON_TOWN)
        await tfl.fetch_timetable(None)
        assert calls == [
            f"https://api.tfl.gov.uk/Line/district/Timetable/{ACTON_TOWN}",
            f"https://api.tfl.gov.uk/Line/piccadilly/Timetable/{ACTON_TOWN}",
        ]


class TestDirections:
    @freeze_time(NOW)
    async def test_westbound_stop_only_gets_westbound_trains(self, tfl_api) -> None:
        tfl = await _stop("Westbound", [WESTBOUND_EALING])
        departures = tfl.get_departures("all")
        assert len(departures) == 6
        assert {d["destination"] for d in departures} == {"Ealing Broadway"}
        assert departures[0]["prediction_type"] == "scheduled+realtime"
        assert all(d["prediction_type"] == "scheduled" for d in departures[1:])
        # The platform live westbound trains use is carried over to the timetabled ones.
        assert {d["platform"] for d in departures} == {"Westbound - 1"}

    @freeze_time(NOW)
    async def test_eastbound_stop_without_live_trains_uses_compass(self, tfl_api) -> None:
        # Only a westbound train is live, so which direction is "Eastbound" has to come from the map.
        tfl = await _stop("Eastbound", [WESTBOUND_EALING])
        departures = tfl.get_departures("all")
        assert departures
        assert all(d["prediction_type"] == "scheduled" for d in departures)
        assert "Ealing Broadway" not in {d["destination"] for d in departures}
        assert {"Upminster", "Edgware Road (Circle Line)"} & {d["destination"] for d in departures}

    @freeze_time(NOW)
    async def test_learned_direction_beats_compass(self, tfl_api) -> None:
        eastbound_live = _live(
            "2026-10-21T16:25:00Z", "Eastbound - Platform 3", "outbound", "940GZZLUUPM", "Upminster", "060"
        )
        tfl = await _stop("Platform 3", [eastbound_live])
        destinations = {d["destination"] for d in tfl.get_departures("all")}
        assert "Ealing Broadway" not in destinations

    @freeze_time(NOW)
    async def test_unknown_direction_shows_no_timetabled_trains(self, tfl_api) -> None:
        # "Platform 9" is neither a compass direction nor seen live: better nothing than both directions mixed.
        tfl = await _stop("Platform 9", [])
        assert tfl.get_departures("all") == []

    @freeze_time(NOW)
    async def test_unfiltered_stop_gets_both_directions(self, tfl_api) -> None:
        tfl = await _stop("", [WESTBOUND_EALING], max_items=10)
        destinations = {d["destination"] for d in tfl.get_departures("all")}
        assert "Ealing Broadway" in destinations
        assert "Upminster" in destinations

    @freeze_time(NOW)
    async def test_branch_destinations_come_from_each_journeys_terminus(self, tfl_api) -> None:
        tfl = await _stop("Eastbound", [], max_items=20)
        destinations = {d["destination"] for d in tfl.get_departures("scheduled")}
        assert len(destinations) > 1


class TestMatching:
    @freeze_time(NOW)
    async def test_live_train_beyond_max_is_not_repeated_as_timetabled(self, tfl_api) -> None:
        later = _live(
            "2026-10-21T16:33:20Z", "Westbound - Platform 1", "inbound", "940GZZLUEBY", "Ealing Broadway", "053"
        )
        tfl = await _stop("Westbound", [WESTBOUND_EALING, later], max_items=1)
        tfl._max_items = None  # look at everything that would be merged
        departures = tfl.get_departures("all")
        near_later = [d for d in departures if "2026-10-21T16:3" in d["expected"] and d["expected"] < "2026-10-21T16:36"]
        assert [d["prediction_type"] for d in near_later] == ["scheduled+realtime"]

    @freeze_time(NOW)
    async def test_train_to_another_branch_is_not_matched(self, tfl_api) -> None:
        # A live eastbound train a minute off a timetabled one, but to a terminus no nearby timetabled train has.
        odd = _live("2026-10-21T16:20:00Z", "Eastbound - Platform 3", "outbound", "940GZZLUXXX", "Nowhere", "070")
        tfl = await _stop("Eastbound", [odd], max_items=20)
        live = [d for d in tfl.get_departures("all") if d["destination"] == "Nowhere"]
        assert [d["prediction_type"] for d in live] == ["realtime"]


class TestSchedules:
    SCHEDULES = [
        {"name": "Monday - Friday"},
        {"name": "Saturdays and Public Holidays"},
        {"name": "Sunday"},
    ]

    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            (date(2026, 10, 21), "Monday - Friday"),
            (date(2026, 10, 24), "Saturdays and Public Holidays"),
            (date(2026, 10, 25), "Sunday"),
            (date(2026, 8, 31), "Saturdays and Public Holidays"),  # summer bank holiday
            (date(2026, 12, 28), "Saturdays and Public Holidays"),  # Boxing Day, moved off Saturday
        ],
    )
    def test_tfl_schedule_names(self, day: date, expected: str) -> None:
        assert _pick_schedule(self.SCHEDULES, day)["name"] == expected

    def test_plain_schedule_preferred_over_school_holiday_variant(self) -> None:
        schedules = [{"name": "Monday to Friday (School Holidays)"}, {"name": "Monday to Friday"}]
        assert _pick_schedule(schedules, date(2026, 10, 21))["name"] == "Monday to Friday"

    @pytest.mark.parametrize(
        "day",
        [date(2026, 1, 1), date(2026, 4, 3), date(2026, 4, 6), date(2026, 5, 4), date(2026, 5, 25),
         date(2026, 8, 31), date(2026, 12, 25), date(2026, 12, 28), date(2027, 12, 27), date(2027, 12, 28),
         date(2028, 1, 3)],
    )
    def test_bank_holidays(self, day: date) -> None:
        assert _is_bank_holiday(day)

    @pytest.mark.parametrize("day", [date(2026, 10, 21), date(2026, 12, 26), date(2027, 12, 25), date(2026, 5, 11)])
    def test_not_bank_holidays(self, day: date) -> None:
        assert not _is_bank_holiday(day)

    @freeze_time("2026-10-21T23:30:00Z")  # 00:30 BST on Thursday: Wednesday's late trains are still running
    async def test_after_midnight_journeys_from_previous_day(self, tfl_api) -> None:
        tfl = await _stop("Westbound", [], max_items=3)
        departures = tfl.get_departures("scheduled")
        assert departures
        first = datetime.fromisoformat(departures[0]["expected"])
        assert first >= datetime(2026, 10, 21, 23, 29, tzinfo=UTC)
