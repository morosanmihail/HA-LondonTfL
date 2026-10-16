"""End-to-end config/options flow tests for the platform-picker step."""
import json
from pathlib import Path

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.london_tfl.const import CONF_PLATFORM, CONF_STOPS, DOMAIN
from custom_components.london_tfl.tfl_data import TfLData

FIXTURES = Path(__file__).parent.parent / "custom_components" / "london_tfl" / "test"
ARRIVALS = json.loads((FIXTURES / "underground.json").read_text())
STATIONS = json.loads((FIXTURES / "stoppoints_victoria.json").read_text())
STATION_ID = STATIONS[0]["stationNaptan"]


@pytest.fixture(autouse=True)
def _fake_network(monkeypatch):
    async def fake_request(url: str):
        if "/line/mode/" in url:
            return json.dumps([{"id": "victoria", "name": "Victoria"}])
        if "/stoppoints" in url:
            return json.dumps(STATIONS)
        raise AssertionError(f"unexpected request {url}")

    async def fake_fetch(self, hass):
        return ARRIVALS

    monkeypatch.setattr("custom_components.london_tfl.config_flow.request", fake_request)
    monkeypatch.setattr(TfLData, "fetch", fake_fetch)


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


def _platform_options(result) -> list[str]:
    field = next(k for k in result["data_schema"].schema if k == CONF_PLATFORM)
    return result["data_schema"].schema[field].config["options"]


async def test_config_flow_offers_live_platforms_then_stores_choice(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"method": "tube"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"line": ["victoria"]}
    )
    assert result["step_id"] == "station"
    assert CONF_PLATFORM not in {str(k) for k in result["data_schema"].schema}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"station": STATION_ID, "max": 3}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "platform"
    assert _platform_options(result) == ["Westbound - 13", "Westbound - 14", "Westbound - 15"]
    assert result["description_placeholders"]["platform_count"] == "3"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PLATFORM: "Westbound - 14"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (stop,) = result["data"][CONF_STOPS]
    assert stop["station"] == STATION_ID
    assert stop[CONF_PLATFORM] == "Westbound - 14"


async def test_config_flow_blank_platform_means_all(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"method": "tube"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"line": ["victoria"]})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"station": STATION_ID, "max": 3}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STOPS][0][CONF_PLATFORM] == ""


async def test_config_flow_add_another_loops_after_platform(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"method": "tube"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"line": ["victoria"]})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"station": STATION_ID, "max": 3, "add_another": True}
    )
    assert result["step_id"] == "platform"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PLATFORM: "Westbound - 13"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_options_edit_shows_dropdown_with_current_value(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_STOPS: [
                {
                    "method": "tube",
                    "line": "victoria",
                    "station": STATION_ID,
                    "max": 3,
                    CONF_PLATFORM: "Northbound - 9",
                    "shortenStationNames": False,
                    "station_display_name": "Somewhere",
                }
            ]
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "edit_stop"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"stop_index": "0"}
    )
    assert result["step_id"] == "edit_station"
    # Current (unlisted) value stays selectable alongside the live platforms.
    assert _platform_options(result) == [
        "Northbound - 9",
        "Westbound - 13",
        "Westbound - 14",
        "Westbound - 15",
    ]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"max": 3, "shortenStationNames": False, CONF_PLATFORM: "Westbound - 15"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STOPS][0][CONF_PLATFORM] == "Westbound - 15"


async def test_options_add_stop_goes_through_platform_step(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_STOPS: []})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_stop"}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"method": "tube"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"line": ["victoria"]}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"station": STATION_ID, "max": 3}
    )
    assert result["step_id"] == "add_platform"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PLATFORM: "Westbound - 13"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STOPS][0][CONF_PLATFORM] == "Westbound - 13"
