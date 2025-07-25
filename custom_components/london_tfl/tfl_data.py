from datetime import datetime
from dateutil import parser

from custom_components.london_tfl.const import (
    TFL_ALT_ARRIVALS_URL,
    TFL_ARRIVALS_URL,
    TFL_BUS_ARRIVALS_URL,
    TFL_TRANSPORT_TYPES,
)


def get_destination(entry, use_destination_name=False):
    if use_destination_name and "destinationName" in entry:
        return entry["destinationName"]
    if "towards" in entry and len(entry["towards"]) > 0:
        return entry["towards"]
    if "destinationName" in entry:
        return entry["destinationName"]
    return ""


def time_to_station(entry, arrival, with_destination=True, style="{0}m {1}s"):
    next_departure_time = (
        parser.parse(arrival).replace(tzinfo=None)
        - datetime.utcnow().replace(tzinfo=None)
    ).total_seconds()
    next_departure_dest = get_destination(entry, True)
    return style.format(
        int(next_departure_time / 60), int(next_departure_time % 60)
    ) + (" to " + next_departure_dest if with_destination else "")


class TfLData:
    def __init__(self, *, method: str, line: str):
        self._raw_result = []
        self._last_update = None
        self._api_json = []
        self._station_name = ""
        self.method = method
        self.line = line

    def populate(self, json_data, filter_platform):
        self._raw_result = json_data
        self.filter_by_platform(filter_platform)
        self._last_update = datetime.now()

    def is_data_stale(self, max_items):
        if len(self._raw_result) > 0:
            # check if there are enough already stored to skip a request
            now = datetime.now().timestamp()
            after_now = []
            try:
                after_now = [
                    item
                    for item in self._raw_result
                    if parser.parse(self._get_expected_arrival(item)).timestamp() > now
                ]
            except Exception:
                after_now = []

            if len(after_now) >= max_items:
                self._raw_result = after_now
                return False
        return True

    def filter_by_platform(self, filter_platform):
        if filter_platform != "":
            self._raw_result = [
                item
                for item in self._raw_result
                if filter_platform in self._get_platform_name(item)
            ]

    def sort_data(self, max_items):
        self._api_json = sorted(
            self._raw_result, key=self._get_expected_arrival, reverse=False
        )[:max_items]

    def get_state(self):
        if len(self._api_json) > 0:
            return parser.parse(self._get_expected_arrival(self._api_json[0])).strftime(
                "%H:%M"
            )
        return "None"

    def is_empty(self):
        return len(self._api_json) == 0

    def _get_expected_departure(self, item) -> str:
        method = "default" if self.method not in TFL_TRANSPORT_TYPES else self.method
        return item[TFL_TRANSPORT_TYPES[method]["expected_departure"]]

    def _get_expected_arrival(self, item) -> str:
        method = "default" if self.method not in TFL_TRANSPORT_TYPES else self.method
        return item[TFL_TRANSPORT_TYPES[method]["expected_arrival"]]

    def _get_platform_name(self, item) -> str:
        method = "default" if self.method not in TFL_TRANSPORT_TYPES else self.method
        platform = item.get(TFL_TRANSPORT_TYPES[method]["platform_name"], "")
        platform = platform.replace("Platform ", "")
        return platform

    def url(self, *, station: str, test: str = "") -> str:
        if self.method in TFL_TRANSPORT_TYPES:
            template = TFL_TRANSPORT_TYPES[self.method]["url"]
        else:
            template = TFL_TRANSPORT_TYPES["default"]["url"]

        return template.format(self.line, station, test)

    def get_departures(self):
        departures = []
        for item in self._api_json:
            method = self.method
            if method not in TFL_TRANSPORT_TYPES:
                method = "default"
            use_destination_name = TFL_TRANSPORT_TYPES[method]["use_destination_name"]
            transport_type = TFL_TRANSPORT_TYPES[method]["transport_type"]
            icon = TFL_TRANSPORT_TYPES[method]["icon"]

            expected_departure = self._get_expected_departure(item)
            expected_arrival = self._get_expected_arrival(item)
            platform = self._get_platform_name(item)
            departure = {
                "time_to_station": time_to_station(item, expected_arrival, False),
                "platform": platform,
                "line": platform,
                "direction": 0,
                "departure": expected_departure,
                "destination": get_destination(item, use_destination_name),
                "time": time_to_station(item, expected_departure, False, "{0}"),
                "expected": expected_arrival,
                "type": transport_type,
                "groupofline": "",
                "icon": icon,
            }

            departures.append(departure)

            if len(self._station_name) == 0:
                self._station_name = item["stationName"]

        return departures

    def get_station_name(self):
        return self._station_name

    def get_last_update(self):
        return self._last_update
