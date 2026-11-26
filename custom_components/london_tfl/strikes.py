"""Strike information for TfL lines, from the TfL line status API.

TfL's strikes page is built from the same data the API serves: each line's status over a date range
(/Line/{ids}/Status/{from}/to/{to}) carries announced strikes as statuses whose reason starts "TUBE STRIKE: ...", with
the exact period they cover. On the day, the live status (/Line/Mode/{modes}/Status) also reports what each line is
actually running "due to strike action". Both are read here; nothing is scraped.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil import parser
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import TFL_COLOUR_CODES
from .network import request

_LOGGER = logging.getLogger(__name__)

LONDON = ZoneInfo("Europe/London")

STRIKE_MODES = ("tube", "elizabeth-line", "overground", "dlr", "tram")
STRIKE_UPDATE_INTERVAL = timedelta(hours=1)
STRIKE_LOOKAHEAD_DAYS = 30
STRIKE_SOON_DAYS = 7

TFL_MODE_STATUS_URL = "https://api.tfl.gov.uk/Line/Mode/{0}/Status"
TFL_LINE_STATUS_RANGE_URL = "https://api.tfl.gov.uk/Line/{0}/Status/{1}/to/{2}"

_STRIKE = re.compile(r"\bstrikes?\b|\bindustrial action\b", re.IGNORECASE)
_HEADING = re.compile(r"^\s*([A-Z0-9][A-Z0-9 &'/\-]{2,40}):\s*(.*)$", re.DOTALL)

# Line colours not in TFL_COLOUR_CODES, which keys the Overground and Elizabeth line by mode.
_EXTRA_COLOURS = {
    "elizabeth": "#6950A1",
    "liberty": "#61686B",
    "lioness": "#FAA61A",
    "mildmay": "#006FE6",
    "suffragette": "#18A95D",
    "weaver": "#9B0058",
    "windrush": "#DC241F",
}

_MODE_NAMES = {
    "tube": "Tube",
    "elizabeth-line": "Elizabeth line",
    "overground": "Overground",
    "dlr": "DLR",
    "tram": "Tram",
}


def is_strike(reason: str | None) -> bool:
    return bool(reason and _STRIKE.search(reason))


def line_colour(line_id: str, mode: str = "") -> str:
    if line_id in _EXTRA_COLOURS:
        return _EXTRA_COLOURS[line_id]
    rgb = TFL_COLOUR_CODES.get(line_id) or TFL_COLOUR_CODES.get(mode) or TFL_COLOUR_CODES["default"]
    return "#{r:02X}{g:02X}{b:02X}".format(**rgb)


def split_reason(reason: str) -> tuple[str, str]:
    """("Tube strike", "Some London Underground Tube drivers ...") from "TUBE STRIKE: Some London ..."."""
    match = _HEADING.match(reason or "")
    if match and match.group(1).upper() == match.group(1):
        return match.group(1).strip().capitalize(), match.group(2).strip()
    return "Strike action", (reason or "").strip()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parser.parse(value)
    except (ValueError, OverflowError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo("UTC"))


def _when(value: datetime) -> str:
    """"Thu 17 Dec 12:00" in London time."""
    return value.astimezone(LONDON).strftime("%a %-d %b %H:%M")


def lines_from_status(current: list) -> list[dict]:
    """Every line in the live status reply as {id, name, mode}, in TfL's order."""
    return [
        {"id": item["id"], "name": item.get("name") or item["id"], "mode": item.get("modeName", "")}
        for item in current
        if isinstance(item, dict) and item.get("id")
    ]


def collect_strikes(current: list, upcoming: list, watched: list[str] | None) -> dict:
    """Strikes on the watched lines (all lines when none are chosen).

    "strikes" holds each announced strike period once, with every watched line it covers. "affected_now" holds what
    each line is running right now because of strike action, from the live status.
    """
    lines = {line["id"]: line for line in lines_from_status(current) + lines_from_status(upcoming)}
    watched_ids = [line_id for line_id in (watched or []) if line_id] or list(lines)
    order = {line_id: index for index, line_id in enumerate(watched_ids)}

    def line_info(line_id: str) -> dict:
        line = lines.get(line_id, {"id": line_id, "name": line_id, "mode": ""})
        return {**line, "colour": line_colour(line_id, line["mode"])}

    periods: dict[tuple, dict] = {}
    for item in upcoming + current:
        if not isinstance(item, dict) or item.get("id") not in order:
            continue
        for status in item.get("lineStatuses") or []:
            reason = status.get("reason") or ""
            if not is_strike(reason):
                continue
            disruption = status.get("disruption") or {}
            if disruption.get("category") == "RealTime":
                continue  # the day's live picture: see affected_now
            for validity in status.get("validityPeriods") or []:
                start, end = _parse(validity.get("fromDate")), _parse(validity.get("toDate"))
                if not start or not end:
                    continue
                key = (start, end, reason.strip())
                entry = periods.get(key)
                if entry is None:
                    title, description = split_reason(reason)
                    entry = periods[key] = {
                        "title": title,
                        "description": description,
                        "start": start,
                        "end": end,
                        "lines": [],
                    }
                if all(line["id"] != item["id"] for line in entry["lines"]):
                    entry["lines"].append(line_info(item["id"]))

    strikes = sorted(periods.values(), key=lambda s: (s["start"], s["end"], s["title"]))
    for strike in strikes:
        strike["lines"].sort(key=lambda line: order[line["id"]])

    # On the day the announcement is replaced by each line's live status, e.g. "Circle Line: No service on the entire
    # line due to strike action." A line can have more than one (Part Suspended here, Minor Delays elsewhere): the
    # worst is shown, TfL numbering severities from 0 (worst) to 10 (Good Service).
    affected_now = []
    for item in current:
        if not isinstance(item, dict) or item.get("id") not in order:
            continue
        live = [
            status
            for status in item.get("lineStatuses") or []
            if is_strike(status.get("reason")) and (status.get("disruption") or {}).get("category") == "RealTime"
        ]
        if not live:
            continue
        worst = min(live, key=lambda status: status.get("statusSeverity", 10))
        starts = [_parse(v.get("fromDate")) for status in live for v in status.get("validityPeriods") or []]
        ends = [_parse(v.get("toDate")) for status in live for v in status.get("validityPeriods") or []]
        affected_now.append(
            {
                **line_info(item["id"]),
                "status": worst.get("statusSeverityDescription", ""),
                "description": (worst.get("reason") or "").strip(),
                "start": min((t for t in starts if t), default=None),
                "end": max((t for t in ends if t), default=None),
            }
        )
    affected_now.sort(key=lambda line: order[line["id"]])

    all_modes = sorted({lines[i]["mode"] for i in watched_ids if i in lines})
    return {
        "strikes": strikes,
        "affected_now": affected_now,
        "watched_lines": [line_info(i)["name"] for i in watched_ids],
        "watched_all": not [line_id for line_id in (watched or []) if line_id],
        "modes": [_MODE_NAMES.get(mode, mode) for mode in all_modes],
    }


def strikes_between(data: dict, start: datetime, end: datetime) -> list[dict]:
    """Announced strike periods overlapping [start, end)."""
    return [s for s in data.get("strikes", []) if s["start"] < end and s["end"] > start]


def strike_today(data: dict, now: datetime) -> list[dict]:
    """Announced strike periods under way now or starting later today (London time).

    A strike that ended at 11:59 this morning no longer counts: what matters is whether the rest of today is affected.
    """
    local = now.astimezone(LONDON)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return strikes_between(data, now, midnight)


def strike_soon(data: dict, now: datetime, days: int = STRIKE_SOON_DAYS) -> list[dict]:
    """Strike periods under way now or starting within the next few days."""
    return strikes_between(data, now, now + timedelta(days=days))


def is_strike_today(data: dict, now: datetime) -> bool:
    return bool(strike_today(data, now) or data.get("affected_now"))


def is_strike_soon(data: dict, now: datetime, days: int = STRIKE_SOON_DAYS) -> bool:
    return bool(strike_soon(data, now, days) or data.get("affected_now"))


def next_strike_start(data: dict, now: datetime) -> datetime | None:
    """Start of the strike under way or next to come, counting today's live strike disruption as one under way."""
    starts = [s["start"] for s in data.get("strikes", []) if s["end"] > now]
    starts += [a["start"] for a in data.get("affected_now", []) if a.get("start")]
    return min(starts) if starts else None


def describe(strike: dict, now: datetime) -> dict:
    """A strike period as plain attribute data (strings, so it is readable in templates and the UI)."""
    lines = strike["lines"]
    return {
        "title": strike["title"],
        "start": strike["start"].astimezone(LONDON).isoformat(),
        "end": strike["end"].astimezone(LONDON).isoformat(),
        "when": f"{_when(strike['start'])} – {_when(strike['end'])}",
        "active": strike["start"] <= now < strike["end"],
        "lines": [line["name"] for line in lines],
        "line_ids": [line["id"] for line in lines],
        "line_colours": [line["colour"] for line in lines],
        "description": strike["description"],
    }


def describe_affected(line: dict) -> dict:
    return {
        "line": line["name"],
        "line_id": line["id"],
        "colour": line["colour"],
        "status": line["status"],
        "description": line["description"],
        "until": line["end"].astimezone(LONDON).isoformat() if line.get("end") else None,
    }


async def _get_json(url: str):
    result = await request(url)
    if not result:
        raise UpdateFailed(f"No reply from TfL for {url}")
    try:
        data = json.loads(result)
    except json.JSONDecodeError as err:
        raise UpdateFailed(f"Could not read TfL's reply for {url}") from err
    if not isinstance(data, list):
        message = data.get("message") if isinstance(data, dict) else ""
        raise UpdateFailed(f"TfL error for {url}: {message or data!r:.200}")
    return data


async def fetch_lines() -> list[dict]:
    """Lines that can be watched for strikes, as {id, name, mode}."""
    return lines_from_status(await _get_json(TFL_MODE_STATUS_URL.format(",".join(STRIKE_MODES))))


async def fetch_strikes(watched: list[str] | None, now: datetime | None = None) -> dict:
    now = now or datetime.now(LONDON)
    current = await _get_json(TFL_MODE_STATUS_URL.format(",".join(STRIKE_MODES)))
    line_ids = [line_id for line_id in (watched or []) if line_id] or [line["id"] for line in lines_from_status(current)]
    today = now.astimezone(LONDON).date()
    upcoming = await _get_json(
        TFL_LINE_STATUS_RANGE_URL.format(
            ",".join(line_ids),
            f"{today.isoformat()}T00:00:00",
            f"{(today + timedelta(days=STRIKE_LOOKAHEAD_DAYS)).isoformat()}T23:59:00",
        )
    )
    return collect_strikes(current, upcoming, watched)


class StrikeCoordinator(DataUpdateCoordinator):
    """Checks TfL for strikes on the watched lines once an hour, shared by the strike sensors of one entry."""

    def __init__(self, hass, watched: list[str] | None) -> None:
        super().__init__(hass, _LOGGER, name="London TfL strikes", update_interval=STRIKE_UPDATE_INTERVAL)
        self.watched = list(watched or [])

    async def _async_update_data(self) -> dict:
        return await fetch_strikes(self.watched)
