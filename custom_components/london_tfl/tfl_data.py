import json
import logging
import uuid
from datetime import datetime, timedelta, UTC
from dateutil import parser
from functools import partial
from typing import Optional, Union
from zoneinfo import ZoneInfo


from custom_components.london_tfl.codes import atco_to_crs
from custom_components.london_tfl.const import (
    TFL_TRANSPORT_TYPES,
    TFL_COLOUR_CODES,
    TFL_TIMETABLE_URL,
    TFL_NR_LINE_TO_TOC,
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
        self._timetable_json = None
        self._timetable_last_fetch = None

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
        except Exception:
            _LOGGER.exception("Unexpected error fetching National Rail departures for %s", self.station)
            return "National Rail fetch error"

        toc = TFL_NR_LINE_TO_TOC.get(self.line)
        if toc:
            filtered = [e.convert() for e in result if e.operator_code == toc]
        else:
            filtered = [e.convert() for e in result if e.operator_id == self.line]

        if not filtered and result:
            _LOGGER.warning(
                "No departures matched operator filter for line %r (TOC=%r); returning all %d trains",
                self.line, toc, len(result),
            )
            filtered = [e.convert() for e in result]

        return filtered

    async def fetch_timetable(self, hass, force: bool = False) -> bool:
        """Fetch timetable data. Returns True if timetable is available."""
        if self.method == "national-rail":
            return False

        now = datetime.now()
        if (
            not force
            and self._timetable_last_fetch is not None
            and (now - self._timetable_last_fetch).total_seconds() < 3 * 24 * 3600
        ):
            return self._timetable_json is not None

        url = TFL_TIMETABLE_URL.format(self.line, self.station)
        try:
            result = await request(url)
            if result:
                parsed = json.loads(result)
                if not isinstance(parsed, dict):
                    _LOGGER.warning("Unexpected timetable response format from %s", url)
                    return False
                self._timetable_json = parsed
                self._timetable_last_fetch = now
                return True
        except Exception:
            _LOGGER.warning("Failed to fetch timetable from %s", url, exc_info=True)
        return False

    def set_timetable(self, timetable_json: dict) -> None:
        """Set timetable data directly (e.g. from a local file for testing)."""
        self._timetable_json = timetable_json
        self._timetable_last_fetch = datetime.now()

    def _get_scheduled_departures_today(self) -> list:
        """Parse timetable and return today's scheduled departures as (datetime_utc, towards) pairs."""
        if not self._timetable_json:
            return []

        try:
            return self._parse_scheduled_departures_today()
        except Exception:
            _LOGGER.warning("Failed to parse timetable data", exc_info=True)
            return []

    def _parse_scheduled_departures_today(self) -> list:
        timetable = self._timetable_json.get("timetable", {})
        routes = timetable.get("routes", [])
        if not routes:
            return []

        departure_stop_id = timetable.get("departureStopId", "")
        interval_offset = 0.0
        if departure_stop_id != self.station:
            for si in routes[0].get("stationIntervals", []):
                for interval in si.get("intervals", []):
                    if interval["stopId"] == self.station:
                        interval_offset = float(interval["timeToArrival"])
                        break
                if interval_offset:
                    break

        towards = ""
        for stop in self._timetable_json.get("stops", []):
            if stop.get("id") == self.station:
                towards = stop.get("towards", "")
                break
        if not towards:
            for stop in self._timetable_json.get("stations", []):
                if stop.get("id") == self.station:
                    towards = stop.get("towards", "")
                    break

        now_london = datetime.now(ZoneInfo("Europe/London"))
        weekday = now_london.weekday()
        if weekday < 5:
            schedule_name = "Monday to Friday"
        elif weekday == 5:
            schedule_name = "Saturday"
        else:
            schedule_name = "Sunday"

        schedules = routes[0].get("schedules", [])
        target_schedule = next(
            (s for s in schedules if s["name"] == schedule_name),
            schedules[0] if schedules else None,
        )
        if not target_schedule:
            return []

        now_utc = datetime.now(UTC)
        window_start = now_utc.timestamp() - 60
        window_end = now_utc.timestamp() + 3600

        result = []
        for journey in target_schedule.get("knownJourneys", []):
            hour = int(journey["hour"])
            minute = int(journey["minute"])
            # Hours >= 24 represent times past midnight on the following day
            if hour >= 24:
                dt_london = now_london.replace(hour=hour - 24, minute=minute, second=0, microsecond=0) + timedelta(days=1)
            else:
                dt_london = now_london.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if interval_offset:
                dt_london = dt_london + timedelta(minutes=interval_offset)
            dt_utc = dt_london.astimezone(UTC)
            ts = dt_utc.timestamp()
            if window_start <= ts <= window_end:
                result.append((dt_utc, towards))

        return result

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

    def _compute_all_departures(self):
        scheduled = self._get_scheduled_departures_today()
        matched_scheduled_indices = set()

        method = self._method_property(TFL_TRANSPORT_TYPES)
        use_destination_name = TFL_TRANSPORT_TYPES[method]["use_destination_name"]
        transport_type = TFL_TRANSPORT_TYPES[method]["transport_type"]
        icon = TFL_TRANSPORT_TYPES[method]["icon"]

        departures = []
        for item in self._api_json:
            expected_departure = self._get_expected_departure(item)
            expected_arrival = self._get_expected_arrival(item)
            platform = self._get_platform_name(item)

            prediction_type = "realtime"
            if scheduled:
                try:
                    arrival_dt = parser.parse(expected_arrival).replace(tzinfo=UTC)
                    for i, (sched_dt, _) in enumerate(scheduled):
                        if abs((arrival_dt - sched_dt).total_seconds()) <= 180:
                            prediction_type = "scheduled+realtime"
                            matched_scheduled_indices.add(i)
                            break
                except Exception:
                    pass

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
                "prediction_type": prediction_type,
            }
            departures.append(departure)

            if len(self._station_name) == 0:
                self._station_name = item.get("stationName", "")

        for i, (sched_dt, towards) in enumerate(scheduled):
            if i in matched_scheduled_indices:
                continue
            sched_iso = sched_dt.isoformat()
            fake_item = {"towards": towards, "destinationName": towards}
            departure = {
                "time_to_station": time_to_station(fake_item, sched_iso, False),
                "platform": self.line,
                "line": self.line,
                "direction": 0,
                "departure": sched_iso,
                "destination": towards,
                "time": time_to_station(fake_item, sched_iso, False, "{0}"),
                "expected": sched_iso,
                "type": transport_type,
                "groupofline": "",
                "icon": icon,
                "prediction_type": "scheduled",
            }
            departures.append(departure)

        departures.sort(key=lambda d: d["expected"])
        return departures

    def _compute_realtime_departures(self):
        """Build departures directly from the realtime API result, no timetable involvement."""
        method = self._method_property(TFL_TRANSPORT_TYPES)
        use_destination_name = TFL_TRANSPORT_TYPES[method]["use_destination_name"]
        transport_type = TFL_TRANSPORT_TYPES[method]["transport_type"]
        icon = TFL_TRANSPORT_TYPES[method]["icon"]

        departures = []
        for item in self._api_json:
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
                "prediction_type": "realtime",
            }
            departures.append(departure)

            if len(self._station_name) == 0:
                self._station_name = item.get("stationName", "")

        departures.sort(key=lambda d: d["expected"])
        return departures

    def get_departures(self, mode: str = "all"):
        """Return departures filtered by mode.

        mode="realtime"  – only entries from the live API, no timetable merging
        mode="scheduled" – only entries present in the timetable
                           (prediction_type "scheduled" or "scheduled+realtime")
        mode="all"       – all departures regardless of source
        """
        if mode == "realtime":
            return self._compute_realtime_departures()
        all_departures = self._compute_all_departures()
        if mode == "scheduled":
            return [d for d in all_departures if d["prediction_type"] in ("scheduled", "scheduled+realtime")]
        return all_departures

    def get_state_from_departures(self, departures: list) -> str:
        """Return HH:MM state string from the first entry in a departures list."""
        if departures:
            return (
                parser.parse(departures[0]["expected"])
                .astimezone(ZoneInfo("Europe/London"))
                .strftime("%H:%M")
            )
        return "None"

    def get_station_name(self):
        return self._station_name

    def get_last_update(self):
        return self._last_update

    def get_line_colours(self):
        if self.line in TFL_COLOUR_CODES:
            return TFL_COLOUR_CODES[self.line]
        method = self._method_property(TFL_COLOUR_CODES)
        return TFL_COLOUR_CODES[method]
