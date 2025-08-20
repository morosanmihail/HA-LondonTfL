from enum import StrEnum
from typing import TypedDict


class TransportType(StrEnum):
    METRO = "METRO"
    BUS = "BUS"
    TRAM = "TRAM"
    TRAIN = "TRAIN"
    SHIP = "SHIP"
    FERRY = "FERRY"
    TAXI = "TAXI"


class DepartureDeviation(TypedDict):
    importance_level: int
    consequence: str
    message: str


class DepartureLine(TypedDict):
    # id: int
    designation: str
    transport_mode: TransportType
    group_of_lines: str


class Departure(TypedDict):
    destination: str
    deviations: list[DepartureDeviation] | None
    # direction: str
    direction_code: int
    # state: str
    # display: str
    # stop_point: dict
    line: DepartureLine
    # scheduled: str
    expected: str


def as_hasl_departures(departures: list[dict]) -> list[Departure]:
    """
    converts the list of departures into a format
    that HASL Departure card (3.2.0+) can understand

    the format can be found [here](https://github.com/hasl-sensor/lovelace-hasl-departure-card/blob/master/src/models.ts)
    """

    return [
        {
            "destination": dep["destination"],
            "deviations": None,
            "direction_code": 0,
            "line": {
                "designation": dep["line"],
                "transport_mode": (
                    TransportType.METRO
                    if dep["type"] == "Metros"
                    else (
                        TransportType.TRAIN
                        if dep["type"] == "Trains"
                        else TransportType.BUS
                    )
                ),
                "group_of_lines": "",
            },
            "expected": dep["expected"],
        }
        for dep in departures
    ]
