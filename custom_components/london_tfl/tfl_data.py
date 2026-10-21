import asyncio
import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta, UTC
from dateutil import parser
from dateutil.easter import easter
from functools import partial
from typing import Optional, Union
from zoneinfo import ZoneInfo


from custom_components.london_tfl.codes import atco_to_crs
from custom_components.london_tfl.const import (
    TFL_API_BASE,
    TFL_TRANSPORT_TYPES,
    TFL_COLOUR_CODES,
    TFL_TIMETABLE_URL,
    TFL_NR_LINE_TO_TOC,
    USE_LDBWS_URL,
)
from custom_components.london_tfl.network import (
    LDBWS,
    LDBWSError,
    RailDataLDBWS,
    request,
)


def get_destination(entry, use_destination_name=False):
    if use_destination_name and "destinationName" in entry:
        return entry["destinationName"]
    if "towards" in entry and len(entry["towards"]) > 0:
        return entry["towards"]
    if "destinationName" in entry:
        return entry["destinationName"]
    return ""


_LOGGER = logging.getLogger(__name__)

LONDON = ZoneInfo("Europe/London")

# How far ahead scheduled departures are merged in (the sensor still caps the list at "max").
SCHEDULE_WINDOW_SECONDS = 2 * 3600
TIMETABLE_CACHE_SECONDS = 24 * 3600
# After a failed timetable fetch, wait this long before asking TfL again.
TIMETABLE_RETRY_SECONDS = 30 * 60
# A live train and a timetabled one this close together (and going the same way) are the same train.
MATCH_SECONDS_SAME_DESTINATION = 180
MATCH_SECONDS_TIME_ONLY = 120

_DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_DAY_TOKEN = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*", re.IGNORECASE)
_RANGE_JOINER = re.compile(r"\s*(?:to|-|–|—|through|thru|until)\s*", re.IGNORECASE)
_STATION_SUFFIXES = (
    " underground station", " dlr station", " rail station", " tram stop", " station",
)
# Platform filters such as "Westbound" name a compass direction: (axis, sign) of the travel it means.
_COMPASS = {"northbound": ("lat", 1), "southbound": ("lat", -1), "eastbound": ("lon", 1), "westbound": ("lon", -1)}

# Timetables are shared network-wide: every stop (and each stop's three sensors) on the same line at the same station
# uses one copy, fetched once a day. Keyed by (line id, station id).
_timetable_cache: dict = {}
_timetable_locks: dict = {}


def clear_timetable_cache() -> None:
    _timetable_cache.clear()
    _timetable_locks.clear()


def _schedule_days(name: str) -> set:
    """Weekdays (Monday=0) a TfL schedule name covers.

    Copes with "Monday to Friday", "Monday - Friday", "Mondays", "Mon - Thu", "Saturday",
    "Saturdays and Public Holidays", "Tuesdays and Wednesdays", "Weekdays", "Weekend" and so on.
    """
    lowered = name.lower()
    days = set()
    if "weekday" in lowered:
        days |= set(range(5))
    if "weekend" in lowered:
        days |= {5, 6}
    if "daily" in lowered or "every day" in lowered:
        days |= set(range(7))

    tokens = list(_DAY_TOKEN.finditer(name))
    i = 0
    while i < len(tokens):
        start = _DAY_INDEX[tokens[i].group(1).lower()]
        if i + 1 < len(tokens) and _RANGE_JOINER.fullmatch(
            name[tokens[i].end() : tokens[i + 1].start()]
        ):
            end = _DAY_INDEX[tokens[i + 1].group(1).lower()]
            if start <= end:
                days |= set(range(start, end + 1))
            else:
                days |= set(range(start, 7)) | set(range(0, end + 1))
            i += 2
        else:
            days.add(start)
            i += 1
    return days


def _is_bank_holiday(day: date) -> bool:
    """England and Wales bank holidays (the fixed rules; one-off extra holidays are not known)."""
    y = day.year
    good_friday = easter(y) - timedelta(days=2)
    holidays = {good_friday, good_friday + timedelta(days=3)}

    def first_monday(month):
        d = date(y, month, 1)
        return d + timedelta(days=(7 - d.weekday()) % 7)

    def last_monday(month):
        d = date(y, month + 1, 1) - timedelta(days=1)
        return d - timedelta(days=d.weekday())

    holidays |= {first_monday(5), last_monday(5), last_monday(8)}

    # New Year's Day, Christmas and Boxing Day move to the next weekday(s) when they fall on a weekend.
    new_year = date(y, 1, 1)
    holidays.add(new_year + timedelta(days={5: 2, 6: 1}.get(new_year.weekday(), 0)))
    christmas, boxing = date(y, 12, 25), date(y, 12, 26)
    if christmas.weekday() == 5:
        christmas, boxing = date(y, 12, 27), date(y, 12, 28)
    elif christmas.weekday() == 6:
        christmas = date(y, 12, 27)
    elif boxing.weekday() == 5:
        boxing = date(y, 12, 28)
    holidays |= {christmas, boxing}
    return day in holidays


def _pick_schedule(schedules: list, service_date: date):
    """The schedule that runs on a date.

    On a bank holiday a schedule that mentions holidays ("Saturdays and Public Holidays") wins. Otherwise the most
    specific name covering the weekday is used, preferring a plain one over a variant such as "Monday to Friday
    (School Holidays)".
    """
    if _is_bank_holiday(service_date):
        holiday = [s for s in schedules if "public holiday" in s.get("name", "").lower()]
        if holiday:
            return holiday[0]

    weekday = service_date.weekday()
    matching = [s for s in schedules if weekday in _schedule_days(s.get("name", ""))]
    if matching:
        return min(
            matching,
            key=lambda s: (len(_schedule_days(s.get("name", ""))), "holiday" in s.get("name", "").lower()),
        )
    if len(schedules) == 1:
        return schedules[0]
    return None


def _strip_station_suffix(name: str) -> str:
    lowered = name.lower()
    for suffix in _STATION_SUFFIXES:
        if lowered.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name.strip()


def _norm_dest(name: str) -> str:
    return _strip_station_suffix(name or "").lower()


def _natural_sort_key(value: str):
    """Sort key that orders embedded numbers numerically ("2" < "10")."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", value)
    ]


def _parse_utc(value: str) -> datetime:
    parsed = parser.parse(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


async def _request_json(url: str):
    """GET a URL and decode its JSON body, or None if there was no usable reply."""
    result = await request(url)
    if not result:
        return None
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        _LOGGER.warning("Failed to interpret timetable JSON from %s: %.200r", url, result)
        return None


def _has_routes(data) -> bool:
    return isinstance(data, dict) and bool((data.get("timetable") or {}).get("routes"))


async def _download_line_timetables(line: str, station: str) -> list:
    """Every timetable TfL has for one line at one station, one per direction.

    Almost every station answers the plain timetable request with a "which route did you mean?" menu instead of a
    timetable. Each option links to a whole direction ("?direction=inbound"), so following the distinct links gives
    the full timetable for the station, all branches included, in one or two requests.
    """
    url = TFL_TIMETABLE_URL.format(line, station)
    first = await _request_json(url)
    if _has_routes(first):
        return [first]
    if not (isinstance(first, dict) and first.get("disambiguation")):
        if first is not None:
            _LOGGER.debug("No timetable from %s: %.200r", url, first)
        return []

    uris = []
    for option in first["disambiguation"].get("disambiguationOptions", []):
        uri = option.get("uri")
        if uri and uri not in uris:
            uris.append(uri)

    timetables = []
    for uri in uris:
        option_url = uri if uri.startswith("http") else TFL_API_BASE + uri
        data = await _request_json(option_url)
        if _has_routes(data):
            timetables.append(data)
        else:
            _LOGGER.debug("No usable timetable from %s", option_url)

    if not timetables:
        _LOGGER.warning(
            "TfL offered %d routes for %s at %s but no timetable could be fetched for any of them",
            len(uris), line, station,
        )
    return timetables


async def _get_line_timetables(line: str, station: str, force: bool = False) -> list:
    key = (line, station)
    lock = _timetable_locks.setdefault(key, asyncio.Lock())
    async with lock:
        now = datetime.now(UTC)
        entry = _timetable_cache.get(key)
        if entry and not force:
            fresh = entry["fetched"] and (now - entry["fetched"]).total_seconds() < TIMETABLE_CACHE_SECONDS
            if entry["timetables"] and fresh:
                return entry["timetables"]
            if entry["retry_after"] and now < entry["retry_after"]:
                return entry["timetables"]

        try:
            timetables = await _download_line_timetables(line, station)
        except Exception:
            _LOGGER.warning("Failed to fetch timetable for %s at %s", line, station, exc_info=True)
            timetables = []

        if timetables:
            entry = {"timetables": timetables, "fetched": now, "retry_after": None}
        else:
            # Keep whatever we had (a day-old timetable beats none) and try again later.
            entry = {
                "timetables": entry["timetables"] if entry else [],
                "fetched": entry["fetched"] if entry else None,
                "retry_after": now + timedelta(seconds=TIMETABLE_RETRY_SECONDS),
            }
        _timetable_cache[key] = entry
        return entry["timetables"]


def time_to_station(entry, arrival, with_destination=True, style="{0}m {1}s"):
    if not arrival:
        return ""
    now = datetime.now(UTC)
    arrival = parser.parse(arrival).replace(tzinfo=UTC)
    next_departure_time = (arrival - now).total_seconds()
    next_departure_dest = get_destination(entry, True)
    return style.format(
        int(next_departure_time / 60), int(next_departure_time % 60)
    ) + (" to " + next_departure_dest if with_destination else "")


class TfLData:
    def __init__(
        self,
        *,
        method: str,
        line: str,
        station: str,
        nr_api_key: Optional[str] = None,
        rdm_api_key: Optional[str] = None,
        crs: Optional[str] = None,
    ):
        self._raw_result = []
        self._last_update = None
        self._api_json = []
        self._live_sorted = []
        self._station_name = ""
        self.method = method
        self.line = line
        self.station = station
        self.nr_api_key = nr_api_key
        self.rdm_api_key = rdm_api_key
        self.crs = crs.upper() if crs else None
        self.__ldbws_client = None  # initialized lazily
        self.__rdm_client = None  # initialized lazily
        self._timetables = []
        self._timetable_last_fetch = None
        self._warned_schedule_weekdays = set()
        self._max_items = None
        self._filter_platform = ""
        # Platform name -> {(line id, "inbound"/"outbound")} seen in live data at this station. Kept across refreshes:
        # where live data is sparse (the District west of Earl's Court) a direction may be seen only now and then.
        self._platform_directions: dict = {}

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
            data = json.loads(result)
            if not isinstance(data, list):
                msg = data.get("message", "Unknown TfL API error") if isinstance(data, dict) else "Unexpected TfL response"
                _LOGGER.warning("TfL API error for %s: %s", url, msg)
                return "Cannot reach TfL"
            return data
        except json.JSONDecodeError:
            _LOGGER.warning(
                "Failed to interpret received JSON for %s: %.200r", url, result
            )
            return "Cannot interpret JSON from TfL"
        except OSError:
            _LOGGER.exception("Internal error during request to %s", url)
            return "Cannot reach TfL"

    async def _fetch_ldbws(self, hass) -> Union[str, list]:
        if not self.rdm_api_key and not self.nr_api_key:
            _LOGGER.warning(
                "Legacy National Rail sensor detected, please recreate to access departure times"
            )
            return "Please recreate this entity to access National Rail departure times"

        try:
            code = self.crs or await atco_to_crs(hass, self.station)
            _LOGGER.debug("Found code for station %s: %s", self.station, code)
        except ValueError:
            _LOGGER.exception("Invalid station code for %s", self.station)
            return "Cannot fetch station code"
        except Exception:
            _LOGGER.exception("Unexpected error resolving CRS code for %s", self.station)
            return "Cannot fetch station code"

        result = None
        if self.rdm_api_key:
            if self.__rdm_client is None:
                self.__rdm_client = RailDataLDBWS(api_key=self.rdm_api_key)
            try:
                result = await self.__rdm_client.get_departures(code)
                _LOGGER.debug("Received Rail Data Marketplace response: %s", result)
            except Exception as e:
                if not self.nr_api_key:
                    _LOGGER.warning(
                        "Failed to get departures for %s (%s) from Rail Data Marketplace: %s",
                        self.station, code, e,
                    )
                    return "Rail Data API error"
                _LOGGER.warning(
                    "Rail Data Marketplace request failed for %s (%s), falling back to legacy Darwin API: %s",
                    self.station, code, e,
                )

        if result is None:
            if self.__ldbws_client is None:
                self.__ldbws_client = await hass.async_add_executor_job(
                    partial(LDBWS, token=self.nr_api_key)
                )
            try:
                result = await self.__ldbws_client.get_departures(code)
                _LOGGER.debug("Received LDBWS response: %s", result)
            except LDBWSError:
                _LOGGER.exception("Failed to get departures for %s", self.station)
                return "LDBWS API error"
            except Exception:
                _LOGGER.exception("Unexpected error fetching National Rail departures for %s", self.station)
                return "National Rail fetch error"

        # A stop may cover several comma-separated lines; keep trains run by any of them.
        line_ids = self.line.split(",")
        tocs = {TFL_NR_LINE_TO_TOC[line] for line in line_ids if line in TFL_NR_LINE_TO_TOC}
        filtered = [
            e.convert()
            for e in result
            if e.operator_code in tocs or e.operator_id in line_ids
        ]

        if not filtered and result:
            _LOGGER.warning(
                "No departures matched operator filter for line %r (TOCs=%r); returning all %d trains",
                self.line, sorted(tocs), len(result),
            )
            filtered = [e.convert() for e in result]

        return filtered


    async def fetch_timetable(self, hass, force: bool = False) -> bool:
        """Fetch timetable data. Returns True if timetable data is available."""
        if self.method == "national-rail":
            return False

        timetables = []
        # A stop can list several lines; the timetable endpoint takes one at a time.
        for line in self.line.split(","):
            line = line.strip()
            if line:
                timetables.extend(await _get_line_timetables(line, self.station, force))

        if timetables:
            self._timetables = timetables
            self._timetable_last_fetch = datetime.now()
        return bool(self._timetables)

    def set_timetable(self, timetable_json: dict) -> None:
        """Set timetable data directly (e.g. from a local file for testing)."""
        self._timetables = [timetable_json] if timetable_json else []
        self._timetable_last_fetch = datetime.now()

    def _get_scheduled_departures_today(self) -> list:
        """Upcoming departures from the timetable(s) that serve this stop's platform filter."""
        if not self._timetables:
            return []

        try:
            return self._parse_scheduled_departures_today()
        except Exception:
            _LOGGER.warning("Failed to parse timetable data", exc_info=True)
            return []

    def _parse_scheduled_departures_today(self) -> list:
        now_utc = datetime.now(UTC)
        window_start = now_utc.timestamp() - 60
        window_end = now_utc.timestamp() + SCHEDULE_WINDOW_SECONDS
        today = now_utc.astimezone(LONDON).date()

        result = []
        seen = set()
        for timetable_json in self._timetables:
            if not self._serves_platform_filter(timetable_json):
                continue
            for departure in self._parse_timetable(timetable_json, today):
                ts = departure["dt"].timestamp()
                if not window_start <= ts <= window_end:
                    continue
                # Several timetables can describe the same trains; list each train once.
                key = (int(ts // 60), departure["line"], _norm_dest(departure["destination"]))
                if key in seen:
                    continue
                seen.add(key)
                result.append(departure)

        result.sort(key=lambda r: r["dt"])
        return result

    def _learned_directions(self, line) -> set:
        """Directions of the given line seen live on the platforms the platform filter keeps."""
        directions = set()
        for platform, pairs in self._platform_directions.items():
            if self._filter_platform in platform:
                directions |= {d for live_line, d in pairs if line is None or live_line == line}
        return directions

    def _serves_platform_filter(self, timetable_json: dict) -> bool:
        """Whether a timetable's trains call at the platforms this stop's filter keeps.

        Live trains carry the platform they use, timetabled ones only a direction. A "Westbound" stop must not be shown
        eastbound trains, so the direction a filtered platform serves is taken from live trains seen there or, until one
        has been seen, from where the timetable's trains head on the map. When neither can tell and the line runs both
        ways here, nothing is shown rather than a mix of both directions.
        """
        if not self._filter_platform:
            return True
        line = timetable_json.get("lineId")
        if self._method_property(TFL_TRANSPORT_TYPES) == "bus":
            # For buses the "platform" is the route name, so the filter picks routes.
            if line and self._filter_platform.lower() not in line.lower():
                return False
        direction = timetable_json.get("direction")
        if not direction:
            return True

        learned = self._learned_directions(line)
        if learned:
            return direction in learned

        heading = self._heads_towards_filter(timetable_json)
        if heading is not None:
            return heading

        directions = {t.get("direction") for t in self._timetables if t.get("lineId") == line}
        return len(directions) <= 1

    def _heads_towards_filter(self, timetable_json: dict):
        """True/False if the platform filter is a compass direction and the timetable's trains do/don't go that way."""
        compass = next((v for k, v in _COMPASS.items() if k in self._filter_platform.lower()), None)
        if compass is None:
            return None
        axis, sign = compass

        coords = {}
        for key in ("stops", "stations"):
            for stop in timetable_json.get(key) or []:
                if stop.get("id") and stop.get(axis) is not None:
                    coords.setdefault(stop["id"], float(stop[axis]))
        here = coords.get(self.station)
        if here is None:
            return None

        votes = 0
        for route in (timetable_json.get("timetable") or {}).get("routes") or []:
            for si in route.get("stationIntervals", []):
                intervals = si.get("intervals") or []
                terminus = coords.get(intervals[-1].get("stopId")) if intervals else None
                if terminus is not None and terminus != here:
                    votes += 1 if (terminus - here) * sign > 0 else -1
        return None if votes == 0 else votes > 0

    def _platform_for(self, line, direction) -> str:
        """The platform name live trains of this line and direction use here, if there is just one."""
        platforms = {
            platform
            for platform, pairs in self._platform_directions.items()
            if (line, direction) in pairs and self._filter_platform in platform
        }
        return platforms.pop() if len(platforms) == 1 else ""

    def _parse_timetable(self, timetable_json: dict, today) -> list:
        timetable = timetable_json.get("timetable") or {}
        routes = timetable.get("routes") or []
        if not routes:
            return []
        departure_stop_id = timetable.get("departureStopId", "")
        line = timetable_json.get("lineId")
        direction = timetable_json.get("direction")
        platform = (
            self._platform_for(line, direction)
            or self._filter_platform
            or timetable_json.get("lineName")
            or line
            or self.line
        )

        names = {}
        towards = ""
        for key in ("stops", "stations"):
            for stop in timetable_json.get(key) or []:
                if stop.get("id") and stop.get("name"):
                    names.setdefault(stop["id"], stop["name"])
                if not towards and stop.get("id") == self.station:
                    towards = stop.get("towards", "")

        departures = []
        for route in routes:
            station_intervals = route.get("stationIntervals", [])
            intervals_by_id = {str(si.get("id")): si.get("intervals", []) for si in station_intervals}
            all_intervals = [i for si in station_intervals for i in si.get("intervals", [])]
            termini = {
                si["intervals"][-1].get("stopId") for si in station_intervals if si.get("intervals")
            }
            # With several different termini (a branching line) the stop's own "towards" text cannot say where a
            # given train is headed, so use each journey's own last stop instead.
            use_terminus = len(termini) > 1 or not towards

            schedules = route.get("schedules", [])
            # Yesterday's schedule matters too: its hour >= 24 journeys run after midnight today.
            for day_offset in (-1, 0):
                service_date = today + timedelta(days=day_offset)
                schedule = _pick_schedule(schedules, service_date)
                if schedule is None:
                    if service_date.weekday() not in self._warned_schedule_weekdays:
                        self._warned_schedule_weekdays.add(service_date.weekday())
                        _LOGGER.warning(
                            "No timetable schedule matches weekday %s for %s at %s (schedules: %s)",
                            service_date.weekday(), line or self.line, self.station,
                            [s.get("name") for s in schedules],
                        )
                    continue

                midnight = datetime(service_date.year, service_date.month, service_date.day, tzinfo=LONDON)
                for journey in schedule.get("knownJourneys", []):
                    minutes = int(journey["hour"]) * 60 + int(journey["minute"])
                    intervals = intervals_by_id.get(str(journey.get("intervalId")), [])

                    offset = 0.0
                    if departure_stop_id != self.station:
                        found = next((i for i in intervals if i.get("stopId") == self.station), None)
                        if found is None and intervals:
                            # This journey's own stop list does not include the stop: it does not call here.
                            continue
                        if found is None:
                            found = next((i for i in all_intervals if i.get("stopId") == self.station), None)
                        if found is not None:
                            offset = float(found.get("timeToArrival") or 0)

                    terminus_id = intervals[-1].get("stopId") if intervals else None
                    destination = towards
                    if use_terminus and terminus_id and terminus_id != self.station and terminus_id in names:
                        destination = _strip_station_suffix(names[terminus_id])

                    # Wall-clock arithmetic, so hour 24 and above roll over into the next day.
                    dt_local = midnight + timedelta(minutes=minutes + offset)
                    departures.append({
                        "dt": dt_local.astimezone(UTC),
                        "destination": destination,
                        "destination_id": terminus_id,
                        "line": line,
                        "direction": direction,
                        "platform": platform,
                    })
        return departures

    def populate(self, json_data, filter_platform):
        self._raw_result = json_data
        self._filter_platform = filter_platform or ""
        self._learn_platform_directions(json_data)
        self.filter_by_platform(filter_platform)
        self._last_update = datetime.now()

    def _learn_platform_directions(self, json_data) -> None:
        for item in json_data:
            direction = item.get("direction")
            if direction in ("inbound", "outbound"):
                pairs = self._platform_directions.setdefault(self._get_platform_name(item), set())
                pairs.add((item.get("lineId"), direction))

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

    def platform_names(self, json_data=None) -> list[str]:
        """Distinct platform names present in the given (or last fetched) data.

        Names are in the same form that filter_by_platform matches against
        (i.e. with any "Platform " prefix removed), so any returned value is
        a valid platform filter. Sorted naturally so "2" comes before "10".
        """
        items = self._raw_result if json_data is None else json_data
        names = {self._get_platform_name(item) for item in items}
        names.discard("")
        return sorted(names, key=_natural_sort_key)

    def sort_data(self, max_items):
        self._max_items = max_items
        sorted_result = sorted(
            self._raw_result, key=self._get_expected_arrival, reverse=False
        )

        deduped = []
        seen_vehicle_ids = set()
        for item in sorted_result:
            vehicle_id = item.get("vehicleId")
            if vehicle_id:
                if vehicle_id in seen_vehicle_ids:
                    continue
                seen_vehicle_ids.add(vehicle_id)
            deduped.append(item)

        # All of it is kept for matching against the timetable: a live train beyond "max" still must not show up again
        # as a timetabled one.
        self._live_sorted = deduped
        self._api_json = deduped[:max_items]

    def get_state(self):
        if len(self._api_json) > 0:
            expected = self._get_expected_arrival(self._api_json[0])
            if not expected:
                return "None"
            return (
                parser.parse(expected)
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
        arrival = item.get(TFL_TRANSPORT_TYPES[method]["expected_arrival"], "")
        if arrival:
            return arrival

        departure = self._get_expected_departure(item)
        if departure:
            return departure

        _LOGGER.warning(
            "No expected arrival or departure time available for station %s (line %s)",
            self.station, self.line,
        )
        return ""

    def _get_platform_name(self, item) -> str:
        method = self._method_property(TFL_TRANSPORT_TYPES)
        platform = item.get(TFL_TRANSPORT_TYPES[method]["platform_name"], "")
        platform = platform.replace("Platform ", "")
        return platform

    def url(self, *, station: str, test: str = "") -> str:
        method = self._method_property(TFL_TRANSPORT_TYPES)
        template = TFL_TRANSPORT_TYPES[method]["url"]
        return template.format(self.line, station, test)

    def _match_live_to_scheduled(self, live: list, scheduled: list, use_destination_name: bool) -> dict:
        """Pair live predictions with the timetabled trains they are. Returns {live index: scheduled index}.

        A pair must be on the same line and in the same direction when both sides say so. Trains to the same terminus
        (by stop id, else by name) within 3 minutes are the same train. When the destination cannot be compared (the
        two feeds word names differently) fall back to time alone within 2 minutes, but only if every candidate goes
        to one place, as on a bus route; on a branching line a train to elsewhere a minute apart is a different train.
        Closest pairs are taken first, so one late train does not steal the slot of the next.
        """
        candidates = []
        for li, item in enumerate(live):
            try:
                arrival = _parse_utc(self._get_expected_arrival(item))
            except Exception:
                continue
            live_line = item.get("lineId")
            live_direction = item.get("direction")
            live_dest_id = item.get("destinationNaptanId")
            live_names = {
                _norm_dest(get_destination(item, use_destination_name)),
                _norm_dest(item.get("destinationName", "")),
            } - {""}

            compatible = [
                (si, s) for si, s in enumerate(scheduled)
                if not (live_line and s["line"] and live_line != s["line"])
                and not (live_direction and s["direction"] and live_direction != s["direction"])
            ]
            same_destination = [
                (si, s) for si, s in compatible
                if (live_dest_id and s["destination_id"] == live_dest_id) or _norm_dest(s["destination"]) in live_names
            ]
            if same_destination:
                pool, tolerance = same_destination, MATCH_SECONDS_SAME_DESTINATION
            elif len({_norm_dest(s["destination"]) for _, s in compatible}) == 1:
                pool, tolerance = compatible, MATCH_SECONDS_TIME_ONLY
            else:
                continue
            for si, s in pool:
                diff = abs((arrival - s["dt"]).total_seconds())
                if diff <= tolerance:
                    candidates.append((diff, li, si))

        matches = {}
        used = set()
        for _, li, si in sorted(candidates):
            if li not in matches and si not in used:
                matches[li] = si
                used.add(si)
        return matches

    def _live_departure(self, item, prediction_type: str) -> dict:
        method = self._method_property(TFL_TRANSPORT_TYPES)
        expected_departure = self._get_expected_departure(item)
        expected_arrival = self._get_expected_arrival(item)
        platform = self._get_platform_name(item)
        if len(self._station_name) == 0:
            self._station_name = item.get("stationName", "")
        return {
            "time_to_station": time_to_station(item, expected_arrival, False),
            "platform": platform,
            "line": platform,
            "direction": 0,
            "departure": expected_departure,
            "destination": get_destination(item, TFL_TRANSPORT_TYPES[method]["use_destination_name"]),
            "time": time_to_station(item, expected_departure, False, "{0}"),
            "expected": expected_arrival,
            "type": TFL_TRANSPORT_TYPES[method]["transport_type"],
            "groupofline": "",
            "icon": TFL_TRANSPORT_TYPES[method]["icon"],
            "prediction_type": prediction_type,
            "current_location": item.get("currentLocation", ""),
        }

    def _scheduled_departure(self, scheduled: dict) -> dict:
        method = self._method_property(TFL_TRANSPORT_TYPES)
        sched_iso = scheduled["dt"].isoformat()
        destination = scheduled["destination"]
        fake_item = {"towards": destination, "destinationName": destination}
        return {
            "time_to_station": time_to_station(fake_item, sched_iso, False),
            "platform": scheduled["platform"],
            "line": scheduled["platform"],
            "direction": 0,
            "departure": sched_iso,
            "destination": destination,
            "time": time_to_station(fake_item, sched_iso, False, "{0}"),
            "expected": sched_iso,
            "type": TFL_TRANSPORT_TYPES[method]["transport_type"],
            "groupofline": "",
            "icon": TFL_TRANSPORT_TYPES[method]["icon"],
            "prediction_type": "scheduled",
            "current_location": "",
        }

    @staticmethod
    def _sort_departures(departures: list) -> list:
        def key(d):
            try:
                return _parse_utc(d["expected"])
            except Exception:
                return datetime.max.replace(tzinfo=UTC)

        departures.sort(key=key)
        return departures

    def _compute_all_departures(self):
        scheduled = self._get_scheduled_departures_today()
        method = self._method_property(TFL_TRANSPORT_TYPES)
        live = self._live_sorted or self._api_json
        matches = (
            self._match_live_to_scheduled(live, scheduled, TFL_TRANSPORT_TYPES[method]["use_destination_name"])
            if scheduled else {}
        )

        departures = [
            self._live_departure(item, "scheduled+realtime" if li in matches else "realtime")
            for li, item in enumerate(live)
        ]
        matched = set(matches.values())
        departures.extend(
            self._scheduled_departure(s) for si, s in enumerate(scheduled) if si not in matched
        )
        return self._sort_departures(departures)

    def _compute_realtime_departures(self):
        """Build departures directly from the realtime API result, no timetable involvement."""
        return self._sort_departures([self._live_departure(item, "realtime") for item in self._api_json])

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
            all_departures = [
                d for d in all_departures if d["prediction_type"] in ("scheduled", "scheduled+realtime")
            ]
        # Live and scheduled together can exceed the stop's "max"; keep just the next few.
        return all_departures[: self._max_items] if self._max_items else all_departures

    def get_state_from_departures(self, departures: list) -> str:
        """Return HH:MM state string from the first entry in a departures list."""
        if departures and departures[0]["expected"]:
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
