import datetime
from typing import List

import aiohttp
import async_timeout
import httpx
from attr import dataclass
from zeep import AsyncClient, xsd
from zeep.exceptions import Fault
from zeep.transports import AsyncTransport


async def fetch(session, url):
    try:
        with async_timeout.timeout(15):
            async with session.get(
                url, headers={"Accept": "application/json"}
            ) as response:
                return await response.text()
    except:
        pass


async def request(url):
    async with aiohttp.ClientSession() as session:
        return await fetch(session, url)


@dataclass
class LDBWSDeparture:
    location_name: str
    platform: str
    operator_id: str
    destination_name: str
    scheduled_departure_time: str

    def convert(self) -> dict:
        now = datetime.datetime.now()
        hours, minutes = self.scheduled_departure_time.split(":")
        if (
            hours.startswith("0") and now.hour > 17
        ):  # i.e. we have a departure at 0X:YZ and it's later in the day so assume it's for the next day
            now += datetime.timedelta(days=1)
        departure_dt = now.replace(
            hour=int(hours), minute=int(minutes), second=0, microsecond=0
        ).isoformat()

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
        result = []
        for service in res.trainServices.service:
            result.append(
                LDBWSDeparture(
                    location_name=res.locationName,
                    platform=service.platform if service.platform is not None else "?",
                    destination_name=service.destination.location[0].locationName,
                    # FIXME: would be better if we could use `operatorCode` but Tfl doesn't expose it on their side
                    operator_id=service.operator.lower().replace(" ", "-"),
                    scheduled_departure_time=service.std,
                    # Note: etd is either `On time` or contains the estimated departure time
                )
            )
        return result
