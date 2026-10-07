"""
Converts ATCO codes (NaPTAN IDs) used by TfL to CRS codes used by LDBWS.

ATCO codes for National Rail stations:
  <3-digit area code><0 or G><TIPLOC>
e.g. 910GKNGX → TIPLOC KNGX, CRS KGX

CRS codes are primarily looked up in the crs.codes station list (a single JSON
file mapping every TIPLOC to its CRS), matching first by TIPLOC and then by
station name, since TfL occasionally uses a different TIPLOC for the same
station (e.g. 910GVICTRIC vs VICTRIA for London Victoria).

As a fallback, CRS codes are fetched from railwaycodes.org.uk (the same source
pyrcs scrapes) but per-letter rather than bulk, which avoids pyrcs's
aggregation bug. Each letter page is fetched once and cached for the process
lifetime.
"""

import asyncio
import html.parser
import json
import logging
import re
import time

import aiohttp

_LOGGER = logging.getLogger(__name__)

_CRS_CODES_URL = "https://crs.codes/data/stations.json"
_CRS_CODES_TTL = 24 * 3600
_RWC_URL = "http://www.railwaycodes.org.uk/crs/crs{}.shtm"
_TFL_STOPPOINT_URL = "https://api.tfl.gov.uk/StopPoint/{}"
_USER_AGENT = "HA-LondonTfL/1.0 (https://github.com/morosanmihail/HA-LondonTfL)"

_letter_cache: dict[str, dict[str, str]] = {}
_crs_cache: dict[str, str] = {}


class CrsCodes:
    """TIPLOC→CRS and station-name→CRS lookups built from crs.codes data."""

    def __init__(self, stations: list[dict]):
        self.by_tiploc: dict[str, str] = {}
        by_name: dict[str, set[str]] = {}
        for station in stations:
            crs = (station.get("crs") or "").strip().upper()
            if not crs:
                continue
            tiploc = (station.get("tiploc") or "").strip().upper()
            if tiploc:
                self.by_tiploc[tiploc] = crs
            # Only public stations take part in name matching; X-prefixed CRS
            # codes are junctions/sidings that would otherwise cause collisions.
            if (
                crs.startswith("X")
                or station.get("hasDepot")
                or station.get("hasSidings")
            ):
                continue
            name = normalise_station_name(station.get("name") or "")
            if name:
                by_name.setdefault(name, set()).add(crs)
        # Drop ambiguous names rather than guess.
        self.by_name: dict[str, str] = {
            name: next(iter(codes)) for name, codes in by_name.items() if len(codes) == 1
        }

    def lookup(self, atco: str, name: str = "") -> str | None:
        """Return the CRS for a TfL ATCO code (and optional TfL commonName)."""
        if len(atco) > 4:
            crs = self.by_tiploc.get(atco[4:].upper())
            if crs:
                return crs
        if name:
            return self.by_name.get(normalise_station_name(name))
        return None


def normalise_station_name(name: str) -> str:
    """Normalise a station name so TfL commonNames and crs.codes names compare equal."""
    name = name.lower().replace("&", "and")
    name = re.sub(
        r"\b(rail station|international|ferry terminal|ferry landing)\b", "", name
    )
    return re.sub(r"[^a-z0-9]", "", name)


_crs_codes: CrsCodes | None = None
_crs_codes_loaded_at: float = 0.0
# After a failed fetch, don't retry for a while: callers (config flow steps and
# every National Rail sensor) would otherwise each wait out the full timeout
# while crs.codes is unreachable, before falling back to other lookups.
_CRS_CODES_RETRY_AFTER = 15 * 60
_crs_codes_failed_at: float | None = None
_crs_codes_lock = asyncio.Lock()


async def load_crs_codes() -> CrsCodes | None:
    """Fetch (or return the cached) crs.codes station list.

    Returns the last good list (possibly stale) or None if it can't be fetched.
    Concurrent callers share a single in-flight request.
    """
    if _crs_codes_usable():
        return _crs_codes
    async with _crs_codes_lock:
        # Another caller may have finished (or failed) the fetch while we waited.
        if _crs_codes_usable():
            return _crs_codes
        return await _fetch_crs_codes()


def _crs_codes_usable() -> bool:
    now = time.monotonic()
    if _crs_codes is not None and now - _crs_codes_loaded_at < _CRS_CODES_TTL:
        return True
    return (
        _crs_codes_failed_at is not None
        and now - _crs_codes_failed_at < _CRS_CODES_RETRY_AFTER
    )


async def _fetch_crs_codes() -> CrsCodes | None:
    global _crs_codes, _crs_codes_loaded_at, _crs_codes_failed_at
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _CRS_CODES_URL,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=15, sock_connect=5),
            ) as resp:
                if resp.status != 200:
                    raise ValueError(f"HTTP {resp.status}")
                stations = await resp.json(content_type=None)
        if not isinstance(stations, list):
            raise ValueError("unexpected station list format")
    except Exception as e:
        _crs_codes_failed_at = time.monotonic()
        _LOGGER.warning(
            "Failed to fetch station list from crs.codes (%s), retrying in %d minutes: %s",
            type(e).__name__, _CRS_CODES_RETRY_AFTER // 60, e,
        )
        return _crs_codes
    _crs_codes = CrsCodes(stations)
    _crs_codes_loaded_at = time.monotonic()
    _crs_codes_failed_at = None
    _LOGGER.debug("Loaded %d TIPLOC→CRS entries from crs.codes", len(_crs_codes.by_tiploc))
    return _crs_codes


def is_valid_crs(code: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]{3}", code or ""))


class _TableParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self._tables: list = []
        self._table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table":
            if self._table is not None:
                self._tables.append(self._table)
            self._table = None
        elif tag == "tr":
            if self._row is not None and self._table is not None:
                self._table.append(self._row)
            self._row = None
        elif tag in ("td", "th"):
            if self._row is not None and self._cell is not None:
                self._row.append("".join(self._cell).strip())
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    @property
    def tables(self):
        return self._tables


def _parse_letter_page(html_content: str) -> dict[str, str]:
    """Extract {TIPLOC: CRS} from a railwaycodes.org.uk letter page."""
    parser = _TableParser()
    parser.feed(html_content)

    for table in parser.tables:
        for i, row in enumerate(table):
            headers = [c.upper().strip() for c in row]
            if "CRS" in headers and "TIPLOC" in headers:
                crs_idx = headers.index("CRS")
                tiploc_idx = headers.index("TIPLOC")
                result = {}
                for data_row in table[i + 1:]:
                    if len(data_row) > max(crs_idx, tiploc_idx):
                        # Cell may contain extra text (e.g. "PETSWD\r\nPETTSWD✖Original code")
                        # when railwaycodes.org.uk annotates a code change; take first token only.
                        raw_tiploc = data_row[tiploc_idx].strip()
                        tiploc = raw_tiploc.split()[0] if raw_tiploc else ""
                        crs = data_row[crs_idx].strip()
                        if tiploc and crs:
                            result[tiploc] = crs
                if result:
                    return result
    return {}


async def _load_letter(letter: str) -> dict[str, str]:
    url = _RWC_URL.format(letter.lower())
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"User-Agent": _USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning("railwaycodes.org.uk returned HTTP %s for letter %s", resp.status, letter)
                    return {}
                html_content = await resp.text(errors="replace")
    except Exception as e:
        _LOGGER.warning("Failed to fetch railwaycodes.org.uk for letter %s: %s", letter, e)
        return {}

    result = _parse_letter_page(html_content)
    _LOGGER.debug("Loaded %d TIPLOC→CRS entries for letter %s", len(result), letter.upper())
    return result


def atco_to_tiploc(atco: str) -> str:
    if len(atco) < 4:
        raise ValueError("ATCO code must be at least 4 characters long")
    if (
        not atco[0].isdigit()
        or not atco[1].isdigit()
        or not atco[2].isdigit()
        or atco[3] not in ["0", "G"]
    ):
        raise ValueError(
            "ATCO code must start with a 3-digit area code followed by either 0 or G"
        )
    return atco[4:]


async def _tfl_api_crs(atco: str) -> str | None:
    from custom_components.london_tfl.network import request

    response = await request(_TFL_STOPPOINT_URL.format(atco))
    if response is None:
        return None
    try:
        data = json.loads(response)
    except (json.JSONDecodeError, ValueError):
        return None
    for prop in data.get("additionalProperties", []):
        if prop.get("key") == "CrsCode":
            return prop["value"]
    return None


async def atco_to_crs(hass, atco: str, name: str = "") -> str:
    """
    Returns the CRS code for a given ATCO code (name is the optional TfL
    commonName, used as a fallback match).
    Raises ValueError if no CRS code can be found.
    """
    if atco in _crs_cache:
        return _crs_cache[atco]

    crs_codes = await load_crs_codes()
    if crs_codes is not None:
        crs = crs_codes.lookup(atco, name)
        if crs:
            _crs_cache[atco] = crs
            _LOGGER.debug("Resolved %s → %s via crs.codes", atco, crs)
            return crs

    tiploc = atco_to_tiploc(atco)
    letter = tiploc[0].upper()

    if letter not in _letter_cache:
        _letter_cache[letter] = await _load_letter(letter)

    if tiploc in _letter_cache[letter]:
        crs = _letter_cache[letter][tiploc]
        _crs_cache[atco] = crs
        _LOGGER.debug("Resolved %s → %s → %s via railwaycodes.org.uk", atco, tiploc, crs)
        return crs

    crs = await _tfl_api_crs(atco)
    if crs:
        _crs_cache[atco] = crs
        _LOGGER.debug("Resolved %s → %s via TfL API fallback", atco, crs)
        return crs

    raise ValueError(f"No CRS code found for ATCO {atco!r} (TIPLOC {tiploc!r})")
