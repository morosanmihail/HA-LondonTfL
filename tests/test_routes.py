"""Direction and calling-at filters for feeds with no direction or usable platform (the Elizabeth line)."""

import json
from pathlib import Path

import pytest

from custom_components.london_tfl import routes as routes_module
from custom_components.london_tfl.routes import (
    LineRoutes,
    compass_between,
    get_line_routes,
)
from custom_components.london_tfl.sensor import LondonTfLSensor
from custom_components.london_tfl.tfl_data import TfLData

FIXTURES = Path(__file__).parent.parent / "custom_components" / "london_tfl" / "test"
SEQUENCES = [
    json.loads((FIXTURES / f"route_sequence_elizabeth_{d}.json").read_text()) for d in ("outbound", "inbound")
]
# Liverpool Street (low level) reports every Elizabeth line train as "Platform Unknown".
LIVST_ARRIVALS = json.loads((FIXTURES / "arrivaldepartures_elizabeth_910GLIVSTLL.json").read_text())

LIVST = "910GLIVSTLL"
STRATFORD = "910GSTFD"


@pytest.fixture
def routes() -> LineRoutes:
    return LineRoutes.from_json(SEQUENCES)


def destinations(data: TfLData) -> list[str]:
    return [item["destinationNaptanId"].removeprefix("910G") for item in data._raw_result]


def test_compass_between_uses_larger_component():
    assert compass_between((51.5, -0.1), (51.5, 0.2)) == "Eastbound"
    assert compass_between((51.5, 0.2), (51.4, -0.1)) == "Westbound"
    assert compass_between((51.5, -0.1), (51.7, -0.12)) == "Northbound"
    assert compass_between((51.5, -0.1), (51.5, -0.1)) is None
    assert compass_between(None, (51.5, -0.1)) is None


def test_directions_offered_from_a_central_station(routes):
    assert routes.directions_from(LIVST) == ["Eastbound", "Westbound"]


@pytest.mark.parametrize(
    "destination, expected",
    [
        ("SHENFLD", "Eastbound"),
        ("ABWDXR", "Eastbound"),
        ("GIDEAPK", "Eastbound"),  # short working, not a route terminus
        ("PADTLL", "Westbound"),
        ("MDNHEAD", "Westbound"),
        ("HTRWTM4", "Westbound"),
        ("RDNGSTN", "Westbound"),
    ],
)
def test_direction_from_liverpool_street(routes, destination, expected):
    assert routes.direction_of(LIVST, "910G" + destination) == expected


def test_heathrow_terminal_4_is_westbound_although_it_lies_east_of_terminals_2_and_3(routes):
    assert routes.direction_of("910GHTRWAPT", "910GHTRWTM4") == "Westbound"
    assert routes.direction_of("910GHTRWAPT", "910GABWDXR") == "Eastbound"


def test_unknown_destination_falls_back_to_the_map(routes):
    routes.coords["910GNOWHERE"] = (51.55, 0.5)
    assert routes.direction_of(LIVST, "910GNOWHERE") == "Eastbound"
    assert routes.direction_of(LIVST, "910GUNMAPPED") is None


def test_calls_at_stratford_from_liverpool_street(routes):
    assert routes.calls_at(LIVST, STRATFORD, "910GSHENFLD")
    assert routes.calls_at(LIVST, STRATFORD, "910GGIDEAPK")
    assert routes.calls_at(LIVST, STRATFORD, STRATFORD)  # a train ending there still calls there
    assert not routes.calls_at(LIVST, STRATFORD, "910GABWDXR")
    assert not routes.calls_at(LIVST, STRATFORD, "910GPADTLL")
    assert not routes.calls_at(LIVST, LIVST, "910GSHENFLD")


def test_stops_after_are_named_and_nearest_first(routes):
    stops = routes.stops_after(LIVST)
    assert list(stops)[:2] == ["910GFRNDXR", "910GWCHAPXR"]
    assert stops[STRATFORD] == "Stratford (London)"
    assert LIVST not in stops
    assert "910GLIVST" not in stops  # the high-level station is on another branch


def make_data(routes=None, **filters) -> TfLData:
    data = TfLData(method="elizabeth-line", line="elizabeth", station=LIVST, **filters)
    if routes is not None:
        data.set_routes(routes)
    data.populate(list(LIVST_ARRIVALS), "")
    return data


def test_unfiltered_stop_keeps_every_train():
    assert len(make_data()._raw_result) == len(LIVST_ARRIVALS)


def test_direction_filter_keeps_only_that_direction(routes):
    east = make_data(routes, direction="Eastbound")
    assert destinations(east) == ["SHENFLD", "ABWDXR", "SHENFLD", "ABWDXR", "SHENFLD"]
    west = make_data(routes, direction="Westbound")
    assert destinations(west) == ["HTRWTM4", "MDNHEAD", "PADTLL", "RDNGSTN", "HTRWTM5"]


def test_calling_at_filter_drops_trains_on_the_other_branch(routes):
    via = make_data(routes, calling_at=STRATFORD)
    assert destinations(via) == ["SHENFLD", "SHENFLD", "SHENFLD"]
    both = make_data(routes, direction="Eastbound", calling_at=STRATFORD)
    assert destinations(both) == ["SHENFLD", "SHENFLD", "SHENFLD"]


def test_without_route_data_a_filtered_stop_shows_nothing_rather_than_both_directions():
    data = make_data(LineRoutes(), direction="Eastbound")
    assert data._raw_result == []
    # its station is still named, from the data before filtering
    assert data.get_station_name() == "Liverpool Street"


def make_sensor(routes=None, mode="realtime", **filters) -> LondonTfLSensor:
    data = TfLData(
        method="elizabeth-line",
        line="elizabeth",
        station=LIVST,
        direction=filters.get("direction"),
        calling_at=filters.get("calling_at"),
    )
    data._station_name = "Liverpool Street"
    if routes is not None:
        data.set_routes(routes)
    sensor = LondonTfLSensor(
        name="London TfL",
        method="elizabeth-line",
        line="elizabeth",
        station=LIVST,
        platform_filter=filters.get("platform", ""),
        max=3,
        shortenStationNames=False,
        departure_mode=mode,
        tfl_data=data,
        direction=filters.get("direction", ""),
        calling_at=filters.get("calling_at", ""),
        calling_at_name=filters.get("calling_at_name", ""),
    )
    sensor._destination = "Paddington"
    return sensor


def test_direction_names_the_stop_and_its_entity(routes):
    sensor = make_sensor(routes, direction="Eastbound")
    assert sensor.name == "Liverpool Street (Eastbound)"
    assert sensor.entity_id == "sensor.london_tfl_elizabeth_910glivstll_eastbound"
    assert sensor.unique_id == "London TfL_elizabeth_910GLIVSTLL_Eastbound"


def test_calling_at_names_the_stop_by_station_name(routes):
    sensor = make_sensor(routes, calling_at=STRATFORD)
    assert sensor.name == "Liverpool Street (via Stratford)"
    assert sensor.entity_id == "sensor.london_tfl_elizabeth_910glivstll_via_910gstfd"
    both = make_sensor(routes, mode="all", direction="Eastbound", calling_at=STRATFORD)
    assert both.name == "Liverpool Street (Eastbound via Stratford) (All)"
    assert both.entity_id == "sensor.london_tfl_elizabeth_910glivstll_eastbound_via_910gstfd_all"


def test_calling_at_name_comes_from_config_until_routes_load():
    sensor = make_sensor(calling_at=STRATFORD, calling_at_name="Stratford")
    assert sensor.name == "Liverpool Street (via Stratford)"


def test_unfiltered_elizabeth_stop_keeps_its_existing_name_and_entity_id():
    sensor = make_sensor()
    assert sensor.name == "Liverpool Street to Paddington"
    assert sensor.entity_id == "sensor.london_tfl_elizabeth_910glivstll"


async def test_route_data_is_fetched_once_and_cached(monkeypatch):
    routes_module.clear_route_cache()
    calls = []

    async def fake_request(url):
        calls.append(url)
        return json.dumps(SEQUENCES[0] if url.endswith("/outbound") else SEQUENCES[1])

    monkeypatch.setattr(routes_module, "request", fake_request)
    first = await get_line_routes("elizabeth")
    second = await get_line_routes("elizabeth")
    assert first.directions_from(LIVST) == ["Eastbound", "Westbound"]
    assert second.routes == first.routes
    assert calls == [
        "https://api.tfl.gov.uk/Line/elizabeth/Route/Sequence/outbound",
        "https://api.tfl.gov.uk/Line/elizabeth/Route/Sequence/inbound",
    ]
    routes_module.clear_route_cache()


async def test_failed_route_fetch_is_not_retried_every_minute(monkeypatch):
    routes_module.clear_route_cache()
    calls = []

    async def no_reply(url):
        calls.append(url)
        return None

    monkeypatch.setattr(routes_module, "request", no_reply)
    assert not await get_line_routes("elizabeth")
    assert not await get_line_routes("elizabeth")
    assert len(calls) == 2  # one attempt (outbound + inbound), then waits before trying again
    routes_module.clear_route_cache()


# Stratford reports the same westbound platform as "Platform 5" for some trains and "Platform Unknown" for others, and a
# disrupted service may be diverted into Liverpool Street's main line station (910GLIVST) instead of the Elizabeth line
# platforms (910GLIVSTLL).
STFD_ARRIVALS = json.loads((FIXTURES / "arrivaldepartures_elizabeth_910GSTFD.json").read_text())


def test_liverpool_street_is_one_station_with_two_stops(routes):
    assert routes.members("HUBLST") == {"910GLIVST", LIVST}
    assert routes.members("910GLIVST") == {"910GLIVST", LIVST}
    assert routes.members(STRATFORD) == {STRATFORD}
    assert routes.station_key("910GLIVST") == "HUBLST"
    assert routes.station_key(STRATFORD) == STRATFORD  # only one stop on the line: keeps its own id
    assert routes.station_name("HUBLST") == "Liverpool Street"
    assert routes.station_name("HUBPAD") == "Paddington"


def test_two_level_stations_are_listed_once(routes):
    stops = routes.stops_after(STRATFORD)
    assert stops["HUBLST"] == "Liverpool Street"
    assert stops["HUBPAD"] == "Paddington"
    assert not {"910GLIVST", LIVST, "910GPADTLL", "910GPADTON"} & set(stops)
    assert list(stops)[:2] == ["HUBLST", "910GMRYLAND"]


def test_calling_at_liverpool_street_includes_trains_diverted_to_the_main_line_station(routes):
    assert routes.calls_at(STRATFORD, "HUBLST", "910GLIVST")
    assert routes.calls_at(STRATFORD, "HUBLST", "910GPADTLL")
    assert routes.calls_at(STRATFORD, "HUBLST", "910GHTRWTM5")
    assert not routes.calls_at(STRATFORD, "HUBLST", "910GSHENFLD")
    assert routes.direction_of(STRATFORD, "HUBLST") == "Westbound"


def test_a_stop_saved_with_one_level_matches_both(routes):
    assert routes.calls_at(STRATFORD, "910GLIVST", "910GPADTLL")
    assert routes.calls_at(STRATFORD, LIVST, "910GLIVST")


def test_without_parent_data_stops_stand_alone():
    bare = LineRoutes.from_json(
        [
            {
                **seq,
                "stopPointSequences": [
                    {"stopPoint": [{k: v for k, v in sp.items() if k != "topMostParentId"} for sp in b["stopPoint"]]}
                    for b in seq["stopPointSequences"]
                ],
            }
            for seq in SEQUENCES
        ]
    )
    assert bare.members("910GLIVST") == {"910GLIVST"}
    assert "910GLIVST" in bare.stops_after(STRATFORD)
    assert bare.calls_at(STRATFORD, "910GLIVST", "910GLIVST")


def stratford(routes, **filters) -> TfLData:
    data = TfLData(method="elizabeth-line", line="elizabeth", station=STRATFORD, **filters)
    data.set_routes(routes)
    data.populate(list(STFD_ARRIVALS), filters.pop("platform", "") if "platform" in filters else "")
    return data


def test_stratford_to_liverpool_street(routes):
    expected = ["PADTLL", "HTRWTM5", "LIVST", "PADTLL"]
    assert destinations(stratford(routes, calling_at="HUBLST")) == expected
    assert destinations(stratford(routes, direction="Westbound")) == expected


def test_platform_filter_at_stratford_loses_trains_reported_as_unknown(routes):
    data = TfLData(method="elizabeth-line", line="elizabeth", station=STRATFORD, direction="Westbound")
    data.set_routes(routes)
    data.populate(list(STFD_ARRIVALS), "5")
    assert destinations(data) == ["PADTLL"]
