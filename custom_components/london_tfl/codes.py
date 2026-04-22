"""
Converts ATCO codes (NaPTAN IDs) used by TfL to CRS codes used by LDBWS.

ATCO codes for National Rail stations:
  <3-digit area code><0 or G><TIPLOC>
e.g. 910GKNGX → TIPLOC KNGX, CRS KGX

CRS codes are fetched from railwaycodes.org.uk (the same source pyrcs scrapes)
but per-letter rather than bulk, which avoids pyrcs's aggregation bug.
Each letter page is fetched once and cached for the process lifetime.
"""

import html.parser
import json
import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)

_RWC_URL = "http://www.railwaycodes.org.uk/crs/crs{}.shtm"
_TFL_STOPPOINT_URL = "https://api.tfl.gov.uk/StopPoint/{}"

_letter_cache: dict[str, dict[str, str]] = {}
_crs_cache: dict[str, str] = {}


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
                        tiploc = data_row[tiploc_idx].strip()
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
                headers={"User-Agent": "HA-LondonTfL/1.0 (https://github.com/morosanmihail/HA-LondonTfL)"},
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


async def atco_to_crs(hass, atco: str) -> str:
    """
    Returns the CRS code for a given ATCO code.
    Raises ValueError if no CRS code can be found.
    """
    if atco in _crs_cache:
        return _crs_cache[atco]

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
