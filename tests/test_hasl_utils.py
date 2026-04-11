from custom_components.london_tfl.hasl_utils import as_hasl_departures, TransportType


METRO_DEPARTURE = {
    "destination": "Stratford",
    "line": "14",
    "direction": 0,
    "type": "Metros",
    "expected": "2025-07-27T16:30:13Z",
}

BUS_DEPARTURE = {
    "destination": "Hackney Wick, Here East",
    "line": "241",
    "direction": 0,
    "type": "Buses",
    "expected": "2025-07-27T16:35:00Z",
}

TRAIN_DEPARTURE = {
    "destination": "London Bridge",
    "line": "1",
    "direction": 0,
    "type": "Trains",
    "expected": "2025-07-27T16:40:00Z",
}


class TestAsHaslDepartures:
    def test_empty_list(self) -> None:
        assert as_hasl_departures([]) == []

    def test_metro_transport_mode(self) -> None:
        result = as_hasl_departures([METRO_DEPARTURE])
        assert result[0]["line"]["transport_mode"] == TransportType.METRO

    def test_bus_transport_mode(self) -> None:
        result = as_hasl_departures([BUS_DEPARTURE])
        assert result[0]["line"]["transport_mode"] == TransportType.BUS

    def test_train_transport_mode(self) -> None:
        result = as_hasl_departures([TRAIN_DEPARTURE])
        assert result[0]["line"]["transport_mode"] == TransportType.TRAIN

    def test_fields_mapped_correctly(self) -> None:
        result = as_hasl_departures([METRO_DEPARTURE])
        dep = result[0]
        assert dep["destination"] == "Stratford"
        assert dep["expected"] == "2025-07-27T16:30:13Z"
        assert dep["line"]["designation"] == "14"
        assert dep["deviations"] is None
        assert dep["direction_code"] == 0

    def test_multiple_departures_preserves_order(self) -> None:
        result = as_hasl_departures([METRO_DEPARTURE, BUS_DEPARTURE, TRAIN_DEPARTURE])
        assert len(result) == 3
        assert result[0]["destination"] == "Stratford"
        assert result[1]["destination"] == "Hackney Wick, Here East"
        assert result[2]["destination"] == "London Bridge"
