import json
import logging
import uuid
from datetime import datetime, UTC
from dateutil import parser
from functools import partial
from typing import Optional, Union
from zoneinfo import ZoneInfo


from custom_components.london_tfl.codes import atco_to_crs
from custom_components.london_tfl.const import (
    TFL_TRANSPORT_TYPES,
    TFL_COLOUR_CODES,
    USE_LDBWS_URL,
)
from custom_components.london_tfl.network import LDBWS, LDBWSError, request


def get_destination(entry, use_destination_name=False):
    if use_destination_name and "destinationName" in entry:
        return entry["destinationName"]
    if "towards" in entry and len(entry["towards"]) > 0:
        return entry["towards"]
    if "destinationName" in entry:
        return entry["destinationName"]
    return ""


_LOGGER = logging.getLogger(__name__)


def time_to_station(entry, arrival, with_destination=True, style="{0}m {1}s"):
    now = datetime.now(UTC)
    arrival = parser.parse(arrival).replace(tzinfo=UTC)
    next_departure_time = (arrival - now).total_seconds()
    next_departure_dest = get_destination(entry, True)
    return style.format(
        int(next_departure_time / 60), int(next_departure_time % 60)
    ) + (" to " + next_departure_dest if with_destination else "")


class TfLData:
    def __init__(
        self, *, method: str, line: str, station: str, nr_api_key: Optional[str] = None
    ):
        self._raw_result = []
        self._last_update = None
        self._api_json = []
        self._station_name = ""
        self.method = method
        self.line = line
        self.station = station
        self.nr_api_key = nr_api_key
        self.__ldbws_client = None  # initialized lazily

    async def fetch(self, hass) -> Union[str, list]:
        url = self.url(station=self.station, test=str(uuid.uuid4()))
        if url == USE_LDBWS_URL:
            return await self._fetch_ldbws(hass)

        try:
            result = await request(
                url,
            )
            if not result:
                _LOGGER.warning("There was no reply from TfL servers for %s", url)
                return "Cannot reach TfL"
            return json.loads(result)
        except json.JSONDecodeError:
            _LOGGER.exception("Failed to interpret received JSON for %s", url)
            return "Cannot interpret JSON from TfL"
        except OSError:
            _LOGGER.exception("Internal error during request to %s", url)
            return "Cannot reach TfL"

    async def _fetch_ldbws(self, hass) -> Union[str, list]:
        if self.nr_api_key is None:
            _LOGGER.warning(
                "Legacy National Rail sensor detected, please recreate to access departure times"
            )
            return "Please recreate this entity to access National Rail departure times"

        if self.__ldbws_client is None:
            self.__ldbws_client = await hass.async_add_executor_job(
                partial(LDBWS, token=self.nr_api_key)
            )
        try:
            code = await atco_to_crs(hass, self.station)
            _LOGGER.debug("Found code for station %s: %s", self.station, code)
            result = await self.__ldbws_client.get_departures(code)
            _LOGGER.debug("Received LDBWS response: %s", result)
        except LDBWSError:
            _LOGGER.exception("Failed to get departures for %s", self.station)
            return "LDBWS API error"
        except ValueError:
            _LOGGER.exception("Invalid station code for %s", self.station)
            return "Cannot fetch station code"

        return [entry.convert() for entry in result if entry.operator_id == self.line]

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
            return (
                parser.parse(self._get_expected_arrival(self._api_json[0]))
                .astimezone(ZoneInfo("Europe/London"))
                .strftime("%H:%M")
            )
        return "None"

    def is_empty(self):
        return len(self._api_json) == 0

    def _method_property(self, const) -> str:
        method = self.method
        if self.line == "thameslink":
            method = self.line
        return "default" if method not in const else method

    def _get_expected_departure(self, item) -> str:
        method = self._method_property(TFL_TRANSPORT_TYPES)
        return item.get(TFL_TRANSPORT_TYPES[method]["expected_departure"], "")

    def _get_expected_arrival(self, item) -> str:
        method = self._method_property(TFL_TRANSPORT_TYPES)
        return item.get(TFL_TRANSPORT_TYPES[method]["expected_arrival"], "")

    def _get_platform_name(self, item) -> str:
        method = self._method_property(TFL_TRANSPORT_TYPES)
        platform = item.get(TFL_TRANSPORT_TYPES[method]["platform_name"], "")
        platform = platform.replace("Platform ", "")
        return platform

    def url(self, *, station: str, test: str = "") -> str:
        method = self._method_property(TFL_TRANSPORT_TYPES)
        template = TFL_TRANSPORT_TYPES[method]["url"]
        return template.format(self.line, station, test)

    def get_departures(self):
        departures = []
        for item in self._api_json:
            method = self._method_property(TFL_TRANSPORT_TYPES)
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

    def get_line_colours(self):
        method = self._method_property(TFL_COLOUR_CODES)
        return TFL_COLOUR_CODES[method]
