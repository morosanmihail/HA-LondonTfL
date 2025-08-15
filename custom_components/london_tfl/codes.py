"""
This file focuses on functions used to transform station codes from one format to another.
Namely:
- ATCO, sometimes called NaPTAN ID (e.g. `910GSUTTON`): used by TfL
- TIPLOC (e.g. `SUTTON`): used by RailData/Darwin
- CRS, aka 3alpha (e.g. `SUO`): used by LDBWS

## Some details on ATCO

Example: `910GSUTTON`

It is formed by a 3 digit area code, followed by `0` or `G`[^1] and then an alpha-numeric code.
In case where a location has multiple stops, it might have the same ATCO code with different numbers at the end.

You can easily get the TIPLOC by removing the first 4 characters of the ATCO.

Check https://mullinscr.github.io/naptan/atco_codes/ for more details.

## Others

There are other codes like NLC or STANOX but we don't need them for this particular use cases.

Check http://www.railwaycodes.org.uk/crs/crs0.shtm or https://wiki.openraildata.com/index.php/Identifying_Locations for more details.

[^1]: Many resources say it's only `0` but as TfL's API proves, that's not the case, and it is also confirmed [here](https://techforum.tfl.gov.uk/t/mapping-of-naptans-to-nlcs/3582/8).

## Challenge

TfL only provides the ATCO code, which means we can easily get the TIPLOC.
Unfortunately, the easiest way to get access to the National Rail data is through LDBWS which uses CRS.

There are other APIs from https://raildata.org.uk/ which can provide this transformation but they aren't very easy to consume (and require registration).
Instead we can leverage PyRCS (https://pypi.org/project/pyrcs/) which wraps access to the http://www.railwaycodes.org.uk database.
This allow transformation between TIPLOC and CRS, closing the gap.
"""

from pyrcs import LocationIdentifiers

_locdata = None


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


def _tiploc_to_crs(tiploc: str) -> str:
    """
    Raises ValueError if None are found.
    Returns the first match if multiple are found.
    """
    global _locdata
    if _locdata is None:
        lid = LocationIdentifiers()
        _locdata = lid.fetch_loc_id()
    locid = _locdata["Location ID"]
    res = locid.loc[locid["TIPLOC"] == tiploc, "CRS"]
    if len(res) > 1:
        return res.iloc[0]
    return res.item()


async def tiploc_to_crs(hass, tiploc: str) -> str:
    return await hass.async_add_executor_job(_tiploc_to_crs, tiploc)


async def atco_to_crs(hass, atco: str) -> str:
    """
    Convenience function, calls `tiploc_to_crs(atco_to_tiploc(atco))`.
    """
    return await tiploc_to_crs(hass, atco_to_tiploc(atco))
