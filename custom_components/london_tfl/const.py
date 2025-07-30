DOMAIN = "london_tfl"

DEFAULT_NAME = "London TfL"
CONF_STOPS = "stops"
CONF_METHOD = "method"
CONF_LINE = "line"
CONF_SHORTEN_STATION_NAMES = "shortenStationNames"
CONF_STATION = "station"
CONF_PLATFORM = "platform"
CONF_MAX = "max"
DEFAULT_MAX = 3
DEFAULT_LINES = {"dlr": "DLR", "jubilee": "Jubilee"}
DEFAULT_METHODS = [
    "tube",
    "dlr",
    "overground",
    "cable-car",
    "tram",
    "river-tour",
    "elizabeth-line",
    "national-rail",
    "bus",
]  # noqa
DEFAULT_ICONS = {"bus": "mdi:bus", "tram": "mdi:tram", "default": "mdi:train"}

LINE_IMAGES = {
    "default": "https://tfl.gov.uk/tfl/common/images/logos/London%20Underground/Roundel/LULRoundel.jpg",  # noqa
    "dlr": "https://tfl.gov.uk/tfl/common/images/logos/Docklands%20Light%20Railway/Roundel/DLRRoundel.jpg",  # noqa
    "london-overground": "https://tfl.gov.uk/tfl/common/images/logos/London%20Overground/Roundel/OvergroundRoundel.jpg",  # noqa
    "tram": "https://tfl.gov.uk/tfl/common/images/logos/London%20Tramlink/Roundel/TramlinkRoundel.jpg",  # noqa
    "tfl-rail": "https://tfl.gov.uk/tfl/common/images/logos/TfL%20Rail/Roundel/TfLRailRoundel.jpg",  # noqa
}

TFL_LINES_URL = "https://api.tfl.gov.uk/line/mode/{0}"
TFL_ARRIVALS_URL = "https://api.tfl.gov.uk/line/{0}/arrivals/{1}?test={2}"
TFL_ALT_ARRIVALS_URL = (
    "https://api.tfl.gov.uk/StopPoint/{1}/arrivaldepartures/?lineIds={0}&test={2}"
)
TFL_BUS_ARRIVALS_URL = "https://api.tfl.gov.uk/StopPoint/{1}/arrivals/?test={2}"
TFL_STATIONS_URL = "https://api.tfl.gov.uk/line/{0}/stoppoints"

SHORTEN_STATION_NAMES = ["Underground Station", "DLR Station", "Rail Station"]


def get_line_image(line):
    return LINE_IMAGES[line] if line in LINE_IMAGES else LINE_IMAGES["default"]


def shortenName(destinationName):
    result = destinationName
    for to_replace in list(SHORTEN_STATION_NAMES):
        result = result.replace(to_replace, "").strip()
    return result


TFL_TRANSPORT_TYPES = {
    "bus": {
        "transport_type": "Buses",
        "icon": DEFAULT_ICONS["bus"],
        "use_destination_name": True,
        "url": TFL_BUS_ARRIVALS_URL,
        "expected_departure": "expectedArrival",
        "expected_arrival": "expectedArrival",
        "platform_name": "lineName",
    },
    "national-rail": {
        "transport_type": "Trains",
        "icon": DEFAULT_ICONS["default"],
        "use_destination_name": False,
        "url": TFL_ALT_ARRIVALS_URL,
        "expected_departure": "scheduledTimeOfDeparture",
        "expected_arrival": "scheduledTimeOfArrival",
        "platform_name": "platformName",
    },
    "thameslink": {
        "transport_type": "Trains",
        "icon": DEFAULT_ICONS["default"],
        "use_destination_name": False,
        "url": TFL_ALT_ARRIVALS_URL,
        "expected_departure": "scheduledTimeOfDeparture",
        "expected_arrival": "scheduledTimeOfArrival",
        "platform_name": "platformName",
    },
    "default": {
        "transport_type": "Metros",
        "icon": DEFAULT_ICONS["default"],
        "use_destination_name": False,
        "url": TFL_ARRIVALS_URL,
        "expected_departure": "expectedArrival",
        "expected_arrival": "expectedArrival",
        "platform_name": "platformName",
    },
}

TFL_COLOUR_CODES = {
    "tube": {"r": 0, "g": 25, "b": 168},
    "dlr": {"r": 0, "g": 175, "b": 173},
    "overground": {"r": 250, "g": 123, "b": 5},
    "elizabeth-line": {"r": 96, "g": 57, "b": 158},
    "bus": {"r": 220, "g": 36, "b": 31},
    "cable-car": {"r": 115, "g": 79, "b": 160},
    "river-tour": {"r": 3, "g": 155, "b": 229},
    "tram": {"r": 95, "g": 181, "b": 38},
    "default": {"r": 0, "g": 25, "b": 168},
}
