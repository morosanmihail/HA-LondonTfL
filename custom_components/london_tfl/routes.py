"""Route-aware filtering: which way a train is going, and whether it calls at a given station.

Some feeds, notably the Elizabeth line's arrivaldepartures, carry neither a direction nor a usable platform (central
London stations report every train as "Platform Unknown"). What they do carry is the train's destination. TfL's
/Line/{line}/Route/Sequence/{direction} lists every route the line runs as an ordered list of stops, so a train's
direction of travel and the stations it passes can be worked out from where it is now and where it is going.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import UTC, datetime, timedelta

from custom_components.london_tfl.const import TFL_ROUTE_SEQUENCE_URL
from custom_components.london_tfl.network import request

_LOGGER = logging.getLogger(__name__)

ROUTE_CACHE_SECONDS = 24 * 3600
ROUTE_RETRY_SECONDS = 30 * 60

COMPASS_DIRECTIONS = ("Northbound", "Southbound", "Eastbound", "Westbound")

_STATION_SUFFIXES = (" Underground Station", " DLR Station", " Rail Station", " Station")

# Route data is shared by every stop on the same line, fetched once a day. Keyed by line id.
_route_cache: dict = {}
_route_locks: dict = {}


def clear_route_cache() -> None:
    _route_cache.clear()


def _clean_name(name: str) -> str:
    for suffix in _STATION_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def compass_between(start, end) -> str | None:
    """Compass direction ("Eastbound"...) of travel from one (lat, lon) to another, by its larger component."""
    if start is None or end is None:
        return None
    dlat = end[0] - start[0]
    # A degree of longitude is shorter than one of latitude away from the equator.
    dlon = (end[1] - start[1]) * math.cos(math.radians((start[0] + end[0]) / 2))
    if dlat == 0 and dlon == 0:
        return None
    if abs(dlon) >= abs(dlat):
        return "Eastbound" if dlon > 0 else "Westbound"
    return "Northbound" if dlat > 0 else "Southbound"


class LineRoutes:
    """The ordered routes of one or more lines, with the name and position of every stop on them."""

    def __init__(self) -> None:
        self.routes: list[list[str]] = []
        self.names: dict[str, str] = {}
        self.coords: dict[str, tuple[float, float]] = {}
        # Stop id -> id of the station complex it belongs to. Liverpool Street's Elizabeth line platforms (910GLIVSTLL)
        # and its main line station (910GLIVST) are separate stops on the line but both sit under HUBLST.
        self.parents: dict[str, str] = {}

    @classmethod
    def from_json(cls, sequences: list) -> LineRoutes:
        """Build from /Line/{line}/Route/Sequence/{direction} replies (any number of lines and directions)."""
        routes = cls()
        for sequence in sequences:
            if not isinstance(sequence, dict):
                continue
            for branch in sequence.get("stopPointSequences") or []:
                for stop in branch.get("stopPoint") or []:
                    stop_id = stop.get("id")
                    if not stop_id:
                        continue
                    if stop.get("name"):
                        routes.names.setdefault(stop_id, _clean_name(stop["name"]))
                    if stop.get("lat") is not None and stop.get("lon") is not None:
                        routes.coords.setdefault(stop_id, (float(stop["lat"]), float(stop["lon"])))
                    if stop.get("topMostParentId"):
                        routes.parents.setdefault(stop_id, stop["topMostParentId"])
            for route in sequence.get("orderedLineRoutes") or []:
                ids = [i for i in route.get("naptanIds") or [] if i]
                if len(ids) > 1 and ids not in routes.routes:
                    routes.routes.append(ids)
        return routes

    def __bool__(self) -> bool:
        return bool(self.routes)

    def name(self, stop_id: str) -> str:
        return self.names.get(stop_id, stop_id)

    def members(self, station_id: str) -> set[str]:
        """Every stop on the line that is part of the same station as this stop id (or station complex id).

        Picking Liverpool Street as a calling-at station has to match trains through the Elizabeth line platforms and
        those diverted into the main line station alike.
        """
        if not station_id:
            return set()
        parent = self.parents.get(station_id, station_id)
        found = {stop for stop, p in self.parents.items() if p == parent}
        return found | ({station_id} if station_id in self.names or not found else set())

    def station_key(self, stop_id: str) -> str:
        """One id per station: the station complex id where the line has several stops in it, else the stop id."""
        parent = self.parents.get(stop_id)
        if parent and parent != stop_id and len(self.members(parent)) > 1:
            return parent
        return stop_id

    def station_name(self, station_id: str) -> str | None:
        """Name of a stop or station complex; for a complex, the shortest of its stops' names ("Liverpool Street")."""
        names = [self.names[m] for m in self.members(station_id) if m in self.names]
        return min(names, key=len) if names else None

    def _route_direction(self, route: list[str]) -> str | None:
        return compass_between(self.coords.get(route[0]), self.coords.get(route[-1]))

    def _legs(self, station: str, destination: str):
        """(route, index of station, index of destination) for each route that runs from station to destination."""
        for route in self.routes:
            if station in route and destination in route:
                here = route.index(station)
                there = route.index(destination)
                if there > here:
                    yield route, here, there

    def direction_of(self, station: str, destination: str) -> str | None:
        """Which way a train from station to destination is travelling, or None if that cannot be told.

        Taken from the overall direction of the line route it runs along, so a short wiggle (Heathrow Terminals 2 & 3
        to Terminal 4 is slightly east) does not flip a westbound train to eastbound. A destination on no route through
        this station falls back to where it lies on the map.
        """
        votes: dict[str, int] = {}
        targets = self.members(destination) or {destination}
        for target in targets:
            for route, _, _ in self._legs(station, target):
                direction = self._route_direction(route)
                if direction:
                    votes[direction] = votes.get(direction, 0) + 1
        if votes:
            return max(votes, key=votes.get)
        return compass_between(self.coords.get(station), self._coords_of(destination))

    def _coords_of(self, station_id: str):
        if station_id in self.coords:
            return self.coords[station_id]
        return next((self.coords[m] for m in sorted(self.members(station_id)) if m in self.coords), None)

    def calls_at(self, station: str, via: str, destination: str) -> bool:
        """Whether a train from station to destination passes through (or ends at) via, on any route it could take.

        via may be a stop or a whole station: any of its stops counts, so "Liverpool Street" matches a train through
        the Elizabeth line platforms and one diverted to terminate in the main line station upstairs.
        """
        stops = self.members(via)
        if not stops or station in stops:
            return False
        return any(
            stops.intersection(route[here + 1 : there + 1]) for route, here, there in self._legs(station, destination)
        )

    def directions_from(self, station: str) -> list[str]:
        """The compass directions trains run in from this station, in a fixed order."""
        found = set()
        for route in self.routes:
            if station in route and route.index(station) < len(route) - 1:
                direction = self._route_direction(route)
                if direction:
                    found.add(direction)
        return [d for d in COMPASS_DIRECTIONS if d in found]

    def stops_after(self, station: str) -> dict[str, str]:
        """Stations reachable from this one without changing, as {station id: name}, nearest first.

        A station with several stops on the line (Liverpool Street, Paddington) is listed once, under its station
        complex id, so that choosing it matches all of them.
        """
        own = self.station_key(station)
        distance: dict[str, int] = {}
        for route in self.routes:
            if station not in route:
                continue
            here = route.index(station)
            for offset, stop in enumerate(route[here + 1 :], start=1):
                key = self.station_key(stop)
                if key == own:
                    continue
                if key not in distance or offset < distance[key]:
                    distance[key] = offset
        ordered = sorted(distance, key=lambda s: (distance[s], self.station_name(s) or s))
        return {key: self.station_name(key) or key for key in ordered}


async def _download_routes(line: str) -> list:
    sequences = []
    for direction in ("outbound", "inbound"):
        url = TFL_ROUTE_SEQUENCE_URL.format(line, direction)
        result = await request(url)
        if not result:
            continue
        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            _LOGGER.warning("Failed to interpret route JSON from %s: %.200r", url, result)
            continue
        if isinstance(data, dict) and data.get("orderedLineRoutes"):
            sequences.append(data)
    return sequences


async def get_line_routes(line_csv: str, force: bool = False) -> LineRoutes:
    """Routes of every line in a comma-separated list, cached for a day; empty if TfL could not be reached."""
    sequences = []
    for line in (part.strip() for part in line_csv.split(",")):
        if line:
            sequences.extend(await _get_sequences(line, force))
    return LineRoutes.from_json(sequences)


async def _get_sequences(line: str, force: bool) -> list:
    lock = _route_locks.setdefault(line, asyncio.Lock())
    async with lock:
        now = datetime.now(UTC)
        entry = _route_cache.get(line)
        if entry and not force:
            fresh = entry["fetched"] and (now - entry["fetched"]).total_seconds() < ROUTE_CACHE_SECONDS
            if entry["sequences"] and fresh:
                return entry["sequences"]
            if entry["retry_after"] and now < entry["retry_after"]:
                return entry["sequences"]

        try:
            sequences = await _download_routes(line)
        except Exception:
            _LOGGER.warning("Failed to fetch route data for %s", line, exc_info=True)
            sequences = []

        if sequences:
            entry = {"sequences": sequences, "fetched": now, "retry_after": None}
        else:
            if not entry or entry["sequences"] or entry["retry_after"] is None:
                _LOGGER.warning(
                    "No route data from TfL for %s; direction and calling-at filters cannot be applied yet", line
                )
            # Keep a day-old copy if there is one: routes rarely change.
            entry = {
                "sequences": entry["sequences"] if entry else [],
                "fetched": entry["fetched"] if entry else None,
                "retry_after": now + timedelta(seconds=ROUTE_RETRY_SECONDS),
            }
        _route_cache[line] = entry
        return entry["sequences"]
