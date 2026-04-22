import asyncio
import datetime
import logging
from typing import List
from zoneinfo import ZoneInfo

import aiohttp
import async_timeout
import httpx
from attr import dataclass
from zeep import AsyncClient, xsd
from zeep.exceptions import Fault
from zeep.transports import AsyncTransport

_LOGGER = logging.getLogger(__name__)


async def fetch(session, url):
    try:
        with async_timeout.timeout(15):
            async with session.get(
                url, headers={"Accept": "application/json"}
            ) as response:
                return await response.text()
    except asyncio.TimeoutError:
        _LOGGER.warning("Request to %s timed out", url)
    except aiohttp.ClientError as e:
        _LOGGER.warning("Request to %s failed: %s", url, e)
    except OSError as e:
        _LOGGER.warning("Request to %s failed: %s", url, e)


async def request(url):
    async with aiohttp.ClientSession() as session:
        return await fetch(session, url)


@dataclass
class LDBWSDeparture:
    location_name: str
    platform: str
    operator_code: str
    operator_id: str
    destination_name: str
    scheduled_departure_time: str

    def convert(self) -> dict:
        london_tz = ZoneInfo("Europe/London")
        now_london = datetime.datetime.now(london_tz)
        hours, minutes = self.scheduled_departure_time.split(":")
        hour_int = int(hours)
        # Only treat as next-day for genuinely overnight departures (before 04:00)
        # when it's already late evening (20:00+). The old startswith("0") check
        # wrongly advanced 09:xx departures.
        if hour_int < 4 and now_london.hour >= 20:
            now_london += datetime.timedelta(days=1)
        departure_dt = now_london.replace(
            hour=hour_int, minute=int(minutes), second=0, microsecond=0
        ).astimezone(datetime.timezone.utc).isoformat()

        return {
            # Arrival is always None so we just use the departure instead
            "scheduledTimeOfDeparture": departure_dt,
            "scheduledTimeOfArrival": departure_dt,
            "platformName": self.platform,
            "destinationName": self.destination_name,
            "stationName": self.location_name,
        }


class LDBWSError(Exception):
    """Raised when an error occurs while interacting with the LDBWS API."""


class LDBWS:
    def __init__(self, *, token: str):
        # FIXME: we should use the default transport but zeep crashes due to changes in httpx
        # see https://github.com/mvantellingen/python-zeep/pull/1462
        httpx_client = httpx.AsyncClient(verify=True)
        wsdl_client = httpx.Client(verify=True, timeout=300)
        self.__client = AsyncClient(
            wsdl="https://lite.realtime.nationalrail.co.uk/OpenLDBWS/wsdl.aspx?ver=2021-11-01",
            transport=AsyncTransport(client=httpx_client, wsdl_client=wsdl_client),
        )

        token_header = xsd.Element(
            "{http://thalesgroup.com/RTTI/2013-11-28/Token/types}AccessToken",
            xsd.ComplexType(
                [
                    xsd.Element(
                        "{http://thalesgroup.com/RTTI/2013-11-28/Token/types}TokenValue",
                        xsd.String(),
                    ),
                ]
            ),
        )
        self.__headers = [token_header(TokenValue=token)]

    async def get_departures(self, crs: str, *, n: int = 10) -> List[LDBWSDeparture]:
        """
        Raises LDBWSError if the request fails.
        """
        try:
            res = await self.__client.service.GetDepartureBoard(
                numRows=n, crs=crs, _soapheaders=self.__headers
            )
        except Fault as e:
            raise LDBWSError("could not get departure board") from e
        if res.trainServices is None:
            return []
        result = []
        for service in res.trainServices.service:
            if (
                service.destination is None
                or not service.destination.location
            ):
                continue
            result.append(
                LDBWSDeparture(
                    location_name=res.locationName,
                    platform=service.platform if service.platform is not None else "?",
                    destination_name=service.destination.location[0].locationName,
                    operator_code=(service.operatorCode or "").upper(),
                    operator_id=service.operator.lower().replace(" ", "-"),
                    scheduled_departure_time=service.std,
                    # Note: etd is either `On time` or contains the estimated departure time
                )
            )
        return result
