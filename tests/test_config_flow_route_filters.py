"""Config/options flow: direction and calling-at choices for Elizabeth line stops."""
import json
from pathlib import Path

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.london_tfl import routes as routes_module
from custom_components.london_tfl.const import (
    CONF_CALLING_AT,
    CONF_CALLING_AT_NAME,
    CONF_DIRECTION,
    CONF_PLATFORM,
    CONF_STOPS,
    DOMAIN,
)
from custom_components.london_tfl.tfl_data import TfLData

FIXTURES = Path(__file__).parent.parent / "custom_components" / "london_tfl" / "test"
SEQUENCES = {
    d: (FIXTURES / f"route_sequence_elizabeth_{d}.json").read_text() for d in ("outbound", "inbound")
}
ARRIVALS = json.loads((FIXTURES / "arrivaldepartures_elizabeth_910GLIVSTLL.json").read_text())
LIVST = "910GLIVSTLL"
STATIONS = [{"stationNaptan": LIVST, "commonName": "Liverpool Street", "additionalProperties": []}]


@pytest.fixture(autouse=True)
def _fake_network(monkeypatch):
    routes_module.clear_route_cache()

    async def fake_request(url: str):
        if "/line/mode/" in url:
            return json.dumps([{"id": "elizabeth", "name": "Elizabeth line"}])
        if "/stoppoints" in url:
            return json.dumps(STATIONS)
        raise AssertionError(f"unexpected request {url}")

    async def fake_routes(url: str):
        return SEQUENCES["outbound" if url.endswith("/outbound") else "inbound"]

    async def fake_fetch(self, hass):
        return list(ARRIVALS)

    async def no_timetable(url: str):
        return None

    monkeypatch.setattr("custom_components.london_tfl.config_flow.request", fake_request)
    monkeypatch.setattr(routes_module, "request", fake_routes)
    monkeypatch.setattr("custom_components.london_tfl.tfl_data.request", no_timetable)
    monkeypatch.setattr(TfLData, "fetch", fake_fetch)
    yield
    routes_module.clear_route_cache()


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


def _options(result, key) -> list:
    field = next(k for k in result["data_schema"].schema if k == key)
    options = result["data_schema"].schema[field].config["options"]
    return [o["value"] if isinstance(o, dict) else o for o in options]


async def _to_platform_step(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"method": "elizabeth-line"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"line": ["elizabeth"]})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"station": LIVST, "max": 3})


async def test_platform_step_offers_direction_and_calling_at(hass) -> None:
    result = await _to_platform_step(hass)
    assert result["step_id"] == "platform"
    assert _options(result, CONF_PLATFORM) == ["Unknown"]
    assert _options(result, CONF_DIRECTION) == ["Eastbound", "Westbound"]
    assert "910GSTFD" in _options(result, CONF_CALLING_AT)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DIRECTION: "Eastbound", CONF_CALLING_AT: "910GSTFD"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (stop,) = result["data"][CONF_STOPS]
    assert stop[CONF_PLATFORM] == ""
    assert stop[CONF_DIRECTION] == "Eastbound"
    assert stop[CONF_CALLING_AT] == "910GSTFD"
    assert stop[CONF_CALLING_AT_NAME] == "Stratford (London)"


async def test_calling_at_the_other_way_is_refused(hass) -> None:
    result = await _to_platform_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DIRECTION: "Westbound", CONF_CALLING_AT: "910GSTFD"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CALLING_AT: "calling_at_wrong_direction"}


async def test_no_filters_chosen_keeps_all_trains(hass) -> None:
    result = await _to_platform_step(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    (stop,) = result["data"][CONF_STOPS]
    assert stop[CONF_DIRECTION] == ""
    assert stop[CONF_CALLING_AT] == ""


async def test_created_sensor_only_shows_trains_via_stratford(hass) -> None:
    result = await _to_platform_step(hass)
    await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_CALLING_AT: "910GSTFD"})
    await hass.async_block_till_done()
    state = hass.states.get("sensor.london_tfl_elizabeth_910glivstll_via_910gstfd")
    assert state is not None
    assert state.name == "Liverpool Street (via Stratford)"
    assert {d["destination"] for d in state.attributes["departures"]} == {"Shenfield Rail Station"}


async def test_options_edit_changes_direction(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_STOPS: [
                {
                    "method": "elizabeth-line",
                    "line": "elizabeth",
                    "station": LIVST,
                    "max": 3,
                    CONF_PLATFORM: "",
                    CONF_DIRECTION: "Westbound",
                    "shortenStationNames": False,
                    "station_display_name": "Liverpool Street",
                }
            ]
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "edit_stop"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"stop_index": "0"})
    assert result["step_id"] == "edit_station"
    assert _options(result, CONF_DIRECTION) == ["Eastbound", "Westbound"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"max": 3, "shortenStationNames": False, CONF_DIRECTION: "Eastbound"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (stop,) = result["data"][CONF_STOPS]
    assert stop[CONF_DIRECTION] == "Eastbound"
    assert stop[CONF_CALLING_AT] == ""


async def test_tube_stops_are_not_offered_route_filters(hass) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"method": "tube"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"line": ["elizabeth"]})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"station": LIVST, "max": 3})
    keys = {str(k) for k in result["data_schema"].schema}
    assert CONF_DIRECTION not in keys and CONF_CALLING_AT not in keys
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert CONF_DIRECTION not in result["data"][CONF_STOPS][0]
