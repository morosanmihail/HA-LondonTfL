import pytest

from custom_components.london_tfl.const import get_line_image, shortenName, LINE_IMAGES


class TestShortenName:
    def test_strips_underground_station(self) -> None:
        assert shortenName("Stratford Underground Station") == "Stratford"

    def test_strips_dlr_station(self) -> None:
        assert shortenName("Canary Wharf DLR Station") == "Canary Wharf"

    def test_strips_rail_station(self) -> None:
        assert shortenName("Paddington Rail Station") == "Paddington"

    def test_no_suffix_unchanged(self) -> None:
        assert shortenName("Waterloo") == "Waterloo"

    def test_strips_whitespace(self) -> None:
        assert shortenName("  Bank Underground Station  ") == "Bank"


class TestGetLineImage:
    def test_known_line_returns_specific_url(self) -> None:
        assert get_line_image("dlr") == LINE_IMAGES["dlr"]

    def test_unknown_line_returns_default(self) -> None:
        assert get_line_image("jubilee") == LINE_IMAGES["default"]

    def test_tram_returns_specific_url(self) -> None:
        assert get_line_image("tram") == LINE_IMAGES["tram"]
