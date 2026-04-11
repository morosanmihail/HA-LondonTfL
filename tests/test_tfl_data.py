import json
import re
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
