import json
import re
from datetime import datetime, UTC, timedelta
from pathlib import Path

import pytest

from custom_components.london_tfl.tfl_data import TfLData

FIXTURES = Path(__file__).parent.parent / "custom_components" / "london_tfl" / "test"


@pytest.fixture
def raw_underground() -> list:
    return json.loads((FIXTURES / "underground.json").read_text())


@pytest.fixture
def raw_bus() -> list:
    return json.loads((FIXTURES / "bus.json").read_text())


@pytest.fixture
def raw_timetable() -> dict:
    return json.loads((FIXTURES / "timetable_490002290ZZ.json").read_text())


@pytest.fixture
def underground_data(raw_underground: list) -> TfLData:
    tfl = TfLData(method="tube", line="jubilee", station="Stratford Underground Station")
    tfl.populate(raw_underground, filter_platform="")
    tfl.sort_data(5)
    return tfl


@pytest.fixture
def bus_data(raw_bus: list) -> TfLData:
    tfl = TfLData(method="bus", line="241", station="Mill Road")
    tfl.populate(raw_bus, filter_platform="241")
    tfl.sort_data(5)
    return tfl


class TestTfLData:
    def test_get_departures(self, underground_data: TfLData) -> None:
        departures = underground_data.get_departures()

        assert len(departures) == 5
        assert departures[0]["destination"] == "Stratford"
        assert departures[0]["type"] == "Metros"
        assert departures[1]["destination"] == "Stratford"
        assert departures[1]["type"] == "Metros"
        assert departures[2]["destination"] == "Stratford"
        assert departures[2]["type"] == "Metros"
        assert departures[3]["destination"] == "Stratford"
        assert departures[3]["type"] == "Metros"

        assert departures[1]["expected"] > departures[0]["expected"]
        assert departures[2]["expected"] > departures[1]["expected"]
        assert departures[3]["expected"] > departures[2]["expected"]
        assert departures[4]["expected"] > departures[3]["expected"]

    def test_get_station_name(self, underground_data: TfLData) -> None:
        underground_data.get_departures()
        assert underground_data.get_station_name() == "Stratford Underground Station"

    def test_get_line_colours(self, underground_data: TfLData) -> None:
        underground_data.get_departures()
        assert underground_data.get_line_colours() == {"r": 160, "g": 165, "b": 169}


class TestBusData:
    def test_get_departures(self, bus_data: TfLData) -> None:
        departures = bus_data.get_departures()

        assert len(departures) == 1
        assert departures[0]["destination"] == "Hackney Wick, Here East"
        assert departures[0]["type"] == "Buses"

    def test_get_station_name(self, bus_data: TfLData) -> None:
        bus_data.get_departures()
        assert bus_data.get_station_name() == "Mill Road"

    def test_get_line_colours(self, bus_data: TfLData) -> None:
        bus_data.get_departures()
        assert bus_data.get_line_colours() == {"r": 220, "g": 36, "b": 31}


class TestTfLDataEmpty:
    def test_is_empty_before_populate(self) -> None:
        tfl = TfLData(method="tube", line="jubilee", station="Somewhere")
        assert tfl.is_empty()

    def test_is_empty_false_after_populate(self, underground_data: TfLData) -> None:
        underground_data.get_departures()
        assert not underground_data.is_empty()

    def test_get_state_returns_none_string_when_empty(self) -> None:
        tfl = TfLData(method="tube", line="jubilee", station="Somewhere")
        assert tfl.get_state() == "None"

    def test_get_station_name_empty_before_departures(self) -> None:
        tfl = TfLData(method="tube", line="jubilee", station="Somewhere")
        assert tfl.get_station_name() == ""

    def test_get_last_update_none_before_populate(self) -> None:
        tfl = TfLData(method="tube", line="jubilee", station="Somewhere")
        assert tfl.get_last_update() is None


class TestTfLDataSortAndFilter:
    def test_sort_data_caps_at_max_items(self, raw_underground: list) -> None:
        tfl = TfLData(method="tube", line="jubilee", station="Stratford Underground Station")
        tfl.populate(raw_underground, filter_platform="")
        tfl.sort_data(2)
        assert len(tfl.get_departures()) == 2

    def test_sort_data_returns_ascending_order(self, underground_data: TfLData) -> None:
        departures = underground_data.get_departures()
        expected_times = [d["expected"] for d in departures]
        assert expected_times == sorted(expected_times)

    def test_filter_by_platform_keeps_matching(self, raw_underground: list) -> None:
        tfl = TfLData(method="tube", line="jubilee", station="Stratford Underground Station")
        tfl.populate(raw_underground, filter_platform="14")
        tfl.sort_data(10)
        departures = tfl.get_departures()
        assert len(departures) > 0
        for dep in departures:
            assert "14" in dep["platform"]

    def test_filter_by_platform_excludes_non_matching(self, raw_underground: list) -> None:
        tfl = TfLData(method="tube", line="jubilee", station="Stratford Underground Station")
        tfl.populate(raw_underground, filter_platform="14")
        tfl.sort_data(10)
        all_tfl = TfLData(method="tube", line="jubilee", station="Stratford Underground Station")
        all_tfl.populate(raw_underground, filter_platform="")
        all_tfl.sort_data(50)
        assert len(tfl.get_departures()) < len(all_tfl.get_departures())

    def test_filter_empty_string_keeps_all(self, raw_underground: list) -> None:
        tfl = TfLData(method="tube", line="jubilee", station="Stratford Underground Station")
        tfl.populate(raw_underground, filter_platform="")
        tfl.sort_data(50)
        assert len(tfl.get_departures()) == len(raw_underground)

    def test_get_state_returns_hhmm_format(self, underground_data: TfLData) -> None:
        underground_data.get_departures()
        state = underground_data.get_state()
        assert re.fullmatch(r"\d{2}:\d{2}", state)

    def test_get_last_update_set_after_populate(self, underground_data: TfLData) -> None:
        assert underground_data.get_last_update() is not None

    def test_platform_name_strips_platform_prefix(self, raw_underground: list) -> None:
        tfl = TfLData(method="tube", line="jubilee", station="Stratford Underground Station")
        tfl.populate(raw_underground, filter_platform="")
        tfl.sort_data(5)
        departures = tfl.get_departures()
        for dep in departures:
            assert not dep["platform"].startswith("Platform ")


class TestTfLDataUrl:
    def test_tube_uses_arrivals_url(self) -> None:
        tfl = TfLData(method="tube", line="jubilee", station="940GZZLUSTD")
        url = tfl.url(station="940GZZLUSTD")
        assert "api.tfl.gov.uk/line/jubilee/arrivals/940GZZLUSTD" in url

    def test_bus_uses_bus_arrivals_url(self) -> None:
        tfl = TfLData(method="bus", line="241", station="490012345X")
        url = tfl.url(station="490012345X")
        assert "api.tfl.gov.uk/StopPoint/490012345X/arrivals" in url

    def test_national_rail_uses_ldbws_sentinel(self) -> None:
        from custom_components.london_tfl.const import USE_LDBWS_URL
        tfl = TfLData(method="national-rail", line="se", station="910GVXHALL")
        assert tfl.url(station="910GVXHALL") == USE_LDBWS_URL

    def test_thameslink_uses_alt_arrivals_url(self) -> None:
        tfl = TfLData(method="tube", line="thameslink", station="910GSTPX")
        url = tfl.url(station="910GSTPX")
        assert "StopPoint/910GSTPX/arrivaldepartures" in url

    def test_elizabeth_line_uses_arrivals_url(self) -> None:
        tfl = TfLData(method="elizabeth-line", line="elizabeth", station="910GLIVST")
        url = tfl.url(station="910GLIVST")
        assert "api.tfl.gov.uk/line/elizabeth/arrivals/910GLIVST" in url


class TestTfLDataColours:
    @pytest.mark.parametrize("method,expected", [
        ("tube", {"r": 0, "g": 25, "b": 168}),
        ("dlr", {"r": 0, "g": 175, "b": 173}),
        ("overground", {"r": 250, "g": 123, "b": 5}),
        ("elizabeth-line", {"r": 96, "g": 57, "b": 158}),
        ("bus", {"r": 220, "g": 36, "b": 31}),
        ("tram", {"r": 95, "g": 181, "b": 38}),
        ("cable-car", {"r": 115, "g": 79, "b": 160}),
        ("river-tour", {"r": 3, "g": 155, "b": 229}),
    ])
    def test_colour_codes_by_method(self, method: str, expected: dict) -> None:
        tfl = TfLData(method=method, line="any", station="any")
        assert tfl.get_line_colours() == expected

    @pytest.mark.parametrize("line,expected", [
        ("bakerloo", {"r": 179, "g": 99, "b": 5}),
        ("central", {"r": 227, "g": 32, "b": 23}),
        ("circle", {"r": 255, "g": 211, "b": 0}),
        ("district", {"r": 0, "g": 120, "b": 42}),
        ("hammersmith-city", {"r": 243, "g": 169, "b": 187}),
        ("jubilee", {"r": 160, "g": 165, "b": 169}),
        ("metropolitan", {"r": 155, "g": 0, "b": 86}),
        ("northern", {"r": 0, "g": 0, "b": 0}),
        ("piccadilly", {"r": 0, "g": 54, "b": 136}),
        ("victoria", {"r": 0, "g": 152, "b": 212}),
        ("waterloo-city", {"r": 149, "g": 205, "b": 186}),
    ])
    def test_colour_codes_by_line(self, line: str, expected: dict) -> None:
        tfl = TfLData(method="tube", line=line, station="any")
        assert tfl.get_line_colours() == expected

    def test_line_takes_priority_over_method(self) -> None:
        tfl = TfLData(method="tube", line="central", station="any")
        assert tfl.get_line_colours() == {"r": 227, "g": 32, "b": 23}

    def test_unknown_line_falls_back_to_method(self) -> None:
        tfl = TfLData(method="tube", line="unknown-line", station="any")
        assert tfl.get_line_colours() == {"r": 0, "g": 25, "b": 168}


def _make_realtime_entry(minutes_from_now: int, line_id="241", station="Test Stop") -> dict:
    expected = (datetime.now(UTC) + timedelta(minutes=minutes_from_now)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {
        "naptanId": "490000000X",
        "stationName": station,
        "lineId": line_id,
        "lineName": line_id,
        "towards": "Custom House",
        "destinationName": "Custom House",
        "expectedArrival": expected,
        "modeName": "bus",
    }


def _make_timetable(stop_id: str, minutes_from_now: list[int], towards="Test Destination") -> dict:
    from zoneinfo import ZoneInfo
    now_london = datetime.now(ZoneInfo("Europe/London"))
    journeys = [
        {"hour": str((now_london + timedelta(minutes=m)).hour),
         "minute": str((now_london + timedelta(minutes=m)).minute),
         "intervalId": 0}
        for m in minutes_from_now
    ]
    schedules = [
        {"name": name, "knownJourneys": journeys}
        for name in ("Monday to Friday", "Saturday", "Sunday")
    ]
    return {
        "timetable": {
            "departureStopId": stop_id,
            "routes": [{"stationIntervals": [], "schedules": schedules}],
        },
        "stops": [{"id": stop_id, "towards": towards, "name": "Test Stop"}],
        "stations": [],
    }


class TestTimetableMerging:
    def test_no_timetable_all_realtime(self) -> None:
        tfl = TfLData(method="bus", line="241", station="490000000X")
        tfl.populate([_make_realtime_entry(5)], filter_platform="241")
        tfl.sort_data(5)
        departures = tfl.get_departures()
        assert all(d["prediction_type"] == "realtime" for d in departures)

    def test_national_rail_no_timetable(self) -> None:
        import asyncio
        tfl = TfLData(method="national-rail", line="se", station="910GVXHALL")
        result = asyncio.get_event_loop().run_until_complete(tfl.fetch_timetable(None))
        assert result is False

    def test_matched_departure_tagged_scheduled_realtime(self) -> None:
        tfl = TfLData(method="bus", line="241", station="490000000X")
        tfl.populate([_make_realtime_entry(10)], filter_platform="241")
        tfl.sort_data(5)
        tfl.set_timetable(_make_timetable("490000000X", [10]))
        departures = tfl.get_departures()
        matched = [d for d in departures if d["prediction_type"] == "scheduled+realtime"]
        assert len(matched) == 1

    def test_unmatched_scheduled_entry_added(self) -> None:
        tfl = TfLData(method="bus", line="241", station="490000000X")
        # Realtime at 5 min, timetable at 30 min — no overlap
        tfl.populate([_make_realtime_entry(5)], filter_platform="241")
        tfl.sort_data(5)
        tfl.set_timetable(_make_timetable("490000000X", [30], towards="Custom House"))
        departures = tfl.get_departures()
        scheduled = [d for d in departures if d["prediction_type"] == "scheduled"]
        assert len(scheduled) == 1
        assert scheduled[0]["destination"] == "Custom House"
        assert scheduled[0]["line"] == "241"

    def test_departures_sorted_by_expected_time(self) -> None:
        tfl = TfLData(method="bus", line="241", station="490000000X")
        tfl.populate([_make_realtime_entry(5)], filter_platform="241")
        tfl.sort_data(5)
        tfl.set_timetable(_make_timetable("490000000X", [20, 40]))
        departures = tfl.get_departures()
        expected_times = [d["expected"] for d in departures]
        assert expected_times == sorted(expected_times)

    def test_timetable_outside_window_not_included(self) -> None:
        tfl = TfLData(method="bus", line="241", station="490000000X")
        tfl.populate([_make_realtime_entry(5)], filter_platform="241")
        tfl.sort_data(5)
        # 90 min is outside the 60-min window
        tfl.set_timetable(_make_timetable("490000000X", [90]))
        departures = tfl.get_departures()
        scheduled = [d for d in departures if d["prediction_type"] == "scheduled"]
        assert len(scheduled) == 0

    @pytest.mark.parametrize("hour", ["24", "25", "26"])
    def test_hour_past_midnight_in_timetable_does_not_raise(self, hour: str) -> None:
        tfl = TfLData(method="bus", line="241", station="490000000X")
        tfl.populate([_make_realtime_entry(5)], filter_platform="241")
        tfl.sort_data(5)
        schedules = [
            {"name": name, "knownJourneys": [{"hour": hour, "minute": "0", "intervalId": 0}]}
            for name in ("Monday to Friday", "Saturday", "Sunday")
        ]
        timetable = {
            "timetable": {
                "departureStopId": "490000000X",
                "routes": [{"stationIntervals": [], "schedules": schedules}],
            },
            "stops": [{"id": "490000000X", "towards": "Test", "name": "Test"}],
            "stations": [],
        }
        tfl.set_timetable(timetable)
        # Must not raise ValueError
        departures = tfl.get_departures()
        assert isinstance(departures, list)

    def test_set_timetable_records_fetch_time(self) -> None:
        tfl = TfLData(method="bus", line="241", station="490000000X")
        assert tfl._timetable_last_fetch is None
        tfl.set_timetable({})
        assert tfl._timetable_last_fetch is not None

    def test_timetable_with_interval_offset(self) -> None:
        stop_id = "490000000X"
        departure_stop = "490000000Y"
        tfl = TfLData(method="bus", line="241", station=stop_id)
        tfl.populate([_make_realtime_entry(15)], filter_platform="241")
        tfl.sort_data(5)
        # Timetable departs origin at T+10, our stop is 5 min into route = T+15 arrival
        from zoneinfo import ZoneInfo
        now_london = datetime.now(ZoneInfo("Europe/London"))
        dep_time = now_london + timedelta(minutes=10)
        timetable = {
            "timetable": {
                "departureStopId": departure_stop,
                "routes": [{
                    "stationIntervals": [{"id": "0", "intervals": [
                        {"stopId": stop_id, "timeToArrival": 5.0}
                    ]}],
                    "schedules": [
                        {"name": name, "knownJourneys": [
                            {"hour": str(dep_time.hour), "minute": str(dep_time.minute), "intervalId": 0}
                        ]}
                        for name in ("Monday to Friday", "Saturday", "Sunday")
                    ],
                }],
            },
            "stops": [{"id": stop_id, "towards": "Custom House", "name": "Test Stop"}],
            "stations": [],
        }
        tfl.set_timetable(timetable)
        departures = tfl.get_departures()
        matched = [d for d in departures if d["prediction_type"] == "scheduled+realtime"]
        assert len(matched) == 1


class TestDeparturesModeFilter:
    def _make_tfl_with_mixed(self) -> TfLData:
        """TfLData with one realtime-only, one scheduled+realtime, one scheduled-only departure."""
        tfl = TfLData(method="bus", line="241", station="490000000X")
        # Realtime at +5 min (no timetable match) and +20 min (timetable match)
        tfl.populate([_make_realtime_entry(5), _make_realtime_entry(20)], filter_platform="241")
        tfl.sort_data(10)
        # Timetable at +20 min (matches realtime) and +40 min (no realtime match)
        tfl.set_timetable(_make_timetable("490000000X", [20, 40], towards="Custom House"))
        return tfl

    def test_mode_all_returns_all(self) -> None:
        tfl = self._make_tfl_with_mixed()
        departures = tfl.get_departures("all")
        types = {d["prediction_type"] for d in departures}
        assert "realtime" in types
        assert "scheduled+realtime" in types
        assert "scheduled" in types
        assert len(departures) == 3

    def test_mode_realtime_excludes_scheduled_only(self) -> None:
        tfl = self._make_tfl_with_mixed()
        departures = tfl.get_departures("realtime")
        for d in departures:
            assert d["prediction_type"] != "scheduled"
        types = {d["prediction_type"] for d in departures}
        assert "realtime" in types
        assert "scheduled+realtime" in types
        assert len(departures) == 2

    def test_mode_scheduled_excludes_realtime_only(self) -> None:
        tfl = self._make_tfl_with_mixed()
        departures = tfl.get_departures("scheduled")
        for d in departures:
            assert d["prediction_type"] != "realtime"
        types = {d["prediction_type"] for d in departures}
        assert "scheduled" in types
        assert "scheduled+realtime" in types
        assert len(departures) == 2

    def test_mode_default_equals_all(self) -> None:
        tfl = self._make_tfl_with_mixed()
        assert tfl.get_departures() == tfl.get_departures("all")

    def test_get_state_from_departures_returns_hhmm(self) -> None:
        tfl = self._make_tfl_with_mixed()
        departures = tfl.get_departures("all")
        state = tfl.get_state_from_departures(departures)
        assert re.fullmatch(r"\d{2}:\d{2}", state)

    def test_get_state_from_departures_empty_returns_none_string(self) -> None:
        tfl = TfLData(method="bus", line="241", station="490000000X")
        assert tfl.get_state_from_departures([]) == "None"
