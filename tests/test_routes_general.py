"""Direction and calling-at filters on lines other than the Elizabeth line: branches, loops and other modes.

The route shapes below follow TfL's /Line/{line}/Route/Sequence data for those lines, cut down to the stops that
matter: the District's seven routes, all through Earl's Court (Ealing Broadway, Kensington (Olympia), Richmond and
Wimbledon to Upminster or Edgware Road), and the Circle line, which TfL describes as one route from Hammersmith to
Edgware Road and on round the loop back to Edgware Road (so Edgware Road appears twice).
"""

import json

import pytest

from custom_components.london_tfl import routes as routes_module
from custom_components.london_tfl.config_flow import _fetch_routes, _route_fields
from custom_components.london_tfl.const import CONF_CALLING_AT, CONF_DIRECTION
from custom_components.london_tfl.routes import LineRoutes
from custom_components.london_tfl.tfl_data import TfLData

STOPS = {
    # District
    "UPM": ("Upminster", 51.559, 0.251),
    "TWH": ("Tower Hill", 51.510, -0.077),
    "VIC": ("Victoria", 51.497, -0.144),
    "ECT": ("Earl's Court", 51.492, -0.195),
    "WBT": ("West Brompton", 51.487, -0.196),
    "WIM": ("Wimbledon", 51.421, -0.206),
    "ACT": ("Acton Town", 51.503, -0.280),
    "KOY": ("Kensington (Olympia)", 51.498, -0.210),
    "RMD": ("Richmond", 51.463, -0.301),
    "TGR": ("Turnham Green", 51.495, -0.254),
    "EBY": ("Ealing Broadway", 51.515, -0.302),
    "HSD": ("High Street Kensington", 51.501, -0.192),
    "ERD": ("Edgware Road", 51.520, -0.168),
    # Circle
    "HSC": ("Hammersmith", 51.493, -0.225),
    "PAH": ("Paddington", 51.519, -0.179),
    "BST": ("Baker Street", 51.522, -0.157),
    "KSX": ("King's Cross St. Pancras", 51.530, -0.124),
    "LST": ("Liverpool Street", 51.517, -0.082),
    "GTR": ("Gloucester Road", 51.494, -0.183),
}


def sequence(routes):
    used = []
    for route in routes:
        used += [s for s in route if s not in used]
    return {
        "stopPointSequences": [
            {
                "stopPoint": [
                    {"id": f"940G{s}", "name": f"{STOPS[s][0]} Underground Station", "lat": STOPS[s][1], "lon": STOPS[s][2],
                     "topMostParentId": f"940G{s}"}
                    for s in used
                ]
            }
        ],
        "orderedLineRoutes": [{"naptanIds": [f"940G{s}" for s in route]} for route in routes],
    }


# TfL's outbound District routes run from the west and south ends towards Upminster and Edgware Road.
DISTRICT_OUT = [
    ["EBY", "ACT", "ECT", "VIC", "TWH", "UPM"],
    ["EBY", "ACT", "ECT", "HSD", "ERD"],
    ["KOY", "ECT", "HSD", "ERD"],
    ["RMD", "TGR", "ECT", "VIC", "TWH", "UPM"],
    ["RMD", "TGR", "ECT", "HSD", "ERD"],
    ["WIM", "WBT", "ECT", "VIC", "TWH", "UPM"],
    ["WIM", "WBT", "ECT", "HSD", "ERD"],
]
CIRCLE = [["HSC", "PAH", "ERD", "BST", "KSX", "LST", "TWH", "VIC", "GTR", "HSD", "ERD"]]


def g(code: str) -> str:
    return f"940G{code}"


@pytest.fixture
def district() -> LineRoutes:
    return LineRoutes.from_json([sequence(DISTRICT_OUT), sequence([list(reversed(r)) for r in DISTRICT_OUT])])


@pytest.fixture
def circle() -> LineRoutes:
    return LineRoutes.from_json([sequence(CIRCLE), sequence([list(reversed(r)) for r in CIRCLE])])


def test_branches_meeting_at_earls_court(district):
    assert district.direction_of(g("ECT"), g("EBY")) == "Westbound"
    assert district.direction_of(g("ECT"), g("RMD")) == "Westbound"
    assert district.direction_of(g("ECT"), g("KOY")) == "Westbound"
    assert district.direction_of(g("ECT"), g("UPM")) == "Eastbound"
    # Edgware Road trains: three of their four routes run east overall (from Ealing, Richmond, Kensington Olympia).
    assert district.direction_of(g("ECT"), g("ERD")) == "Eastbound"
    # Wimbledon trains: one westbound route (from Upminster), one southbound (from Edgware Road). Most routes through
    # Earl's Court run east or west, so westbound, like the Ealing and Richmond trains.
    assert district.direction_of(g("ECT"), g("WIM")) == "Westbound"


def test_only_directions_that_some_train_takes_are_offered(district):
    # The Wimbledon - Edgware Road route runs north/south overall, but no train from Earl's Court resolves to those.
    assert district.directions_from(g("ECT")) == ["Eastbound", "Westbound"]


def test_on_the_wimbledon_branch_calling_at_is_the_way_to_get_every_train_into_town(district):
    # Known limit of a per-route compass: Upminster and Edgware Road trains leave Wimbledon the same way but run on
    # routes pointing different ways overall, so they count as eastbound and northbound respectively...
    assert district.direction_of(g("WIM"), g("UPM")) == "Eastbound"
    assert district.direction_of(g("WIM"), g("ERD")) == "Northbound"
    assert district.directions_from(g("WIM")) == ["Northbound", "Eastbound"]
    # ...so to catch both, pick a station they share.
    assert district.calls_at(g("WIM"), g("ECT"), g("UPM"))
    assert district.calls_at(g("WIM"), g("ECT"), g("ERD"))


def test_calling_at_picks_a_branch(district):
    assert district.calls_at(g("VIC"), g("ACT"), g("EBY"))
    assert not district.calls_at(g("VIC"), g("ACT"), g("WIM"))
    assert district.calls_at(g("HSD"), g("WBT"), g("WIM"))


def test_a_loop_gives_no_direction_but_still_calling_at(circle):
    assert LineRoutes.is_loop([g(s) for s in CIRCLE[0]])
    assert circle.directions_from(g("BST")) == []
    assert circle.direction_of(g("BST"), g("ERD")) is None
    # Baker Street round to Edgware Road the long way passes Liverpool Street and Victoria...
    assert circle.calls_at(g("BST"), g("LST"), g("ERD"))
    assert circle.calls_at(g("BST"), g("VIC"), g("ERD"))
    # ...and from Edgware Road (on the route twice) the loop runs back round to Edgware Road.
    assert circle.calls_at(g("ERD"), g("KSX"), g("ERD"))


def test_loop_stations_are_listed_once_for_calling_at(circle):
    stops = circle.stops_after(g("PAH"))
    assert list(stops).count(g("ERD")) == 1
    assert stops[g("ERD")] == "Edgware Road"


def test_route_fields_for_a_tube_stop(district):
    fields = {str(k): v for k, v in _route_fields(district, g("ECT")).items()}
    assert set(fields) == {CONF_DIRECTION, CONF_CALLING_AT}
    assert fields[CONF_DIRECTION].config["options"] == ["Eastbound", "Westbound"]


def test_circle_stop_is_offered_calling_at_only(circle):
    fields = {str(k) for k in _route_fields(circle, g("BST"))}
    assert fields == {CONF_CALLING_AT}


@pytest.mark.parametrize("method, line", [("tube", "district"), ("overground", "mildmay"), ("dlr", "dlr"), ("tram", "tram")])
async def test_route_data_is_fetched_for_every_rail_mode(monkeypatch, method, line):
    routes_module.clear_route_cache()
    urls = []

    async def fake_request(url):
        urls.append(url)
        return json.dumps(sequence(DISTRICT_OUT))

    monkeypatch.setattr(routes_module, "request", fake_request)
    routes = await _fetch_routes({"method": method, "line": line, "station": g("ECT")})
    assert routes
    assert urls[0] == f"https://api.tfl.gov.uk/Line/{line}/Route/Sequence/outbound"
    routes_module.clear_route_cache()


def test_tube_arrivals_are_filtered_by_destination(district):
    def arrival(destination, platform):
        return {
            "naptanId": g("ECT"),
            "stationName": "Earl's Court Underground Station",
            "lineId": "district",
            "platformName": platform,
            "destinationNaptanId": g(destination),
            "expectedArrival": "2026-11-26T18:30:00Z",
        }

    feed = [
        arrival("EBY", "Westbound - Platform 3"),
        arrival("WIM", "Westbound - Platform 4"),
        arrival("UPM", "Eastbound - Platform 1"),
        arrival("ERD", "Northbound - Platform 2"),
    ]
    data = TfLData(method="tube", line="district", station=g("ECT"), calling_at=g("WBT"))
    data.set_routes(district)
    data.populate(feed, "")
    assert [i["destinationNaptanId"] for i in data._raw_result] == [g("WIM")]
