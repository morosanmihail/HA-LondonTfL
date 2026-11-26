"""Strike entities in Home Assistant, and the options flow that turns them on."""

import json
from pathlib import Path

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.london_tfl import strikes as strikes_module
from custom_components.london_tfl.const import (
    CONF_STOPS,
    CONF_STRIKE_LINES,
    CONF_STRIKES,
    CONF_STRIKES_ENABLED,
    DOMAIN,
)

FIXTURES = Path(__file__).parent.parent / "custom_components" / "london_tfl" / "test"
NOW = (FIXTURES / "strikes_status_now.json").read_text()
RANGE = (FIXTURES / "strikes_status_range.json").read_text()
QUIET_NOW = json.dumps(
    [{**line, "lineStatuses": [{"statusSeverity": 10, "statusSeverityDescription": "Good Service"}]}
     for line in json.loads(NOW)]
)

SUMMARY = "sensor.london_tfl_strikes"
TODAY = "binary_sensor.london_tfl_strike_today"
SOON = "binary_sensor.london_tfl_strike_within_7_days"


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


@pytest.fixture
def tfl(monkeypatch):
    """Fake TfL: live status (switchable to a quiet day) and the 30-day range."""
    replies = {"now": NOW}
    urls = []

    async def fake_request(url):
        urls.append(url)
        return replies["now"] if "/Mode/" in url else RANGE

    monkeypatch.setattr(strikes_module, "request", fake_request)
    monkeypatch.setattr("custom_components.london_tfl.config_flow.request", fake_request)
    return replies, urls


async def _setup(hass, strikes):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_STOPS: []}, options={CONF_STOPS: [], CONF_STRIKES: strikes})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_strike_day(hass, tfl, freezer) -> None:
    freezer.move_to("2026-11-26 15:30:00+00:00")
    await _setup(hass, {CONF_STRIKES_ENABLED: True, CONF_STRIKE_LINES: []})

    summary = hass.states.get(SUMMARY)
    assert summary.state == "2026-11-26T12:12:00+00:00"  # today's disruption began
    assert summary.attributes["strike_count"] == 2
    assert summary.attributes["strikes"][0]["when"] == "Thu 17 Dec 12:00 – Fri 18 Dec 11:59"
    assert [a["line"] for a in summary.attributes["affected_now"]] == ["Central", "Circle", "District"]
    assert summary.attributes["watching_all_lines"] is True
    assert hass.states.get(TODAY).state == "on"
    assert hass.states.get(SOON).state == "on"


async def test_quiet_day_with_strikes_announced(hass, tfl, freezer) -> None:
    replies, _ = tfl
    replies["now"] = QUIET_NOW
    freezer.move_to("2026-12-12 09:00:00+00:00")
    await _setup(hass, {CONF_STRIKES_ENABLED: True, CONF_STRIKE_LINES: ["victoria"]})

    assert hass.states.get(SUMMARY).state == "2026-12-17T12:00:00+00:00"
    assert hass.states.get(SUMMARY).attributes["strikes"][0]["lines"] == ["Victoria"]
    assert hass.states.get(TODAY).state == "off"
    assert hass.states.get(SOON).state == "on"
    assert hass.states.get(SOON).attributes["next_strike"] == "Thu 17 Dec 12:00 – Fri 18 Dec 11:59"


async def test_lines_without_strikes(hass, tfl, freezer) -> None:
    replies, _ = tfl
    replies["now"] = QUIET_NOW
    freezer.move_to("2026-12-12 09:00:00+00:00")
    await _setup(hass, {CONF_STRIKES_ENABLED: True, CONF_STRIKE_LINES: ["elizabeth"]})
    assert hass.states.get(SUMMARY).state == "unknown"
    assert hass.states.get(SUMMARY).attributes["strike_count"] == 0
    assert hass.states.get(TODAY).state == "off"
    assert hass.states.get(SOON).state == "off"


async def test_off_by_default(hass, tfl) -> None:
    await _setup(hass, {})
    assert hass.states.get(SUMMARY) is None
    assert hass.states.get(TODAY) is None
    _, urls = tfl
    assert urls == []  # TfL is not asked about strikes at all


async def test_options_flow_turns_strikes_on_then_off(hass, tfl, freezer) -> None:
    freezer.move_to("2026-11-26 15:30:00+00:00")
    entry = await _setup(hass, {})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "strikes" in result["menu_options"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "strikes"})
    assert result["step_id"] == "strikes"
    field = next(k for k in result["data_schema"].schema if k == CONF_STRIKE_LINES)
    options = result["data_schema"].schema[field].config["options"]
    assert {"value": "victoria", "label": "Victoria (Tube)"} in options
    assert {"value": "elizabeth", "label": "Elizabeth line (Elizabeth line)"} in options or any(
        o["value"] == "elizabeth" for o in options
    )
    assert result["description_placeholders"]["line_count"] == "20"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_STRIKES_ENABLED: True, CONF_STRIKE_LINES: ["circle"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STRIKES] == {CONF_STRIKES_ENABLED: True, CONF_STRIKE_LINES: ["circle"]}
    assert result["data"][CONF_STOPS] == []
    await hass.async_block_till_done()
    assert hass.states.get(TODAY).state == "on"
    assert [a["line"] for a in hass.states.get(SUMMARY).attributes["affected_now"]] == ["Circle"]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "strikes"})
    field = next(k for k in result["data_schema"].schema if k == CONF_STRIKE_LINES)
    assert field.description["suggested_value"] == ["circle"]
    await hass.config_entries.options.async_configure(result["flow_id"], {CONF_STRIKES_ENABLED: False})
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    assert registry.async_get(SUMMARY) is None
    assert registry.async_get(TODAY) is None
    assert registry.async_get(SOON) is None


async def test_editing_stops_keeps_strike_settings(hass, tfl, freezer) -> None:
    freezer.move_to("2026-11-26 15:30:00+00:00")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_STOPS: []},
        options={
            CONF_STOPS: [
                {
                    "method": "elizabeth-line",
                    "line": "elizabeth",
                    "station": "910GSTFD",
                    "max": 3,
                    "platform": "",
                    "shortenStationNames": False,
                    "station_display_name": "Stratford",
                }
            ],
            CONF_STRIKES: {CONF_STRIKES_ENABLED: True, CONF_STRIKE_LINES: []},
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "remove_stop"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"stop_indices": ["0"]})
    assert result["data"][CONF_STRIKES] == {CONF_STRIKES_ENABLED: True, CONF_STRIKE_LINES: []}


async def test_tfl_unreachable_does_not_stop_the_entry_loading(hass, monkeypatch) -> None:
    async def no_reply(url):
        return None

    monkeypatch.setattr(strikes_module, "request", no_reply)
    entry = await _setup(hass, {CONF_STRIKES_ENABLED: True})
    assert entry.state.value == "loaded"
    assert hass.states.get(SUMMARY).state == "unavailable"
    assert hass.states.get(TODAY).state == "unavailable"


async def test_removing_a_stop_still_removes_its_sensors_and_keeps_strike_entities(hass, tfl, freezer, monkeypatch) -> None:
    """The registry clean-up that drops a removed stop's sensors must not touch the strike entities."""
    from custom_components.london_tfl.tfl_data import TfLData

    async def no_departures(self, hass):
        return []

    async def no_timetable(url):
        return None

    monkeypatch.setattr(TfLData, "fetch", no_departures)
    monkeypatch.setattr("custom_components.london_tfl.tfl_data.request", no_timetable)
    freezer.move_to("2026-11-26 15:30:00+00:00")

    def stop(station):
        return {
            "method": "tube", "line": "victoria", "station": station, "max": 3, "platform": "",
            "shortenStationNames": False, "station_display_name": station,
        }

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_STOPS: []},
        options={
            CONF_STOPS: [stop("940GZZLUVIC"), stop("940GZZLUOXC")],
            CONF_STRIKES: {CONF_STRIKES_ENABLED: True, CONF_STRIKE_LINES: []},
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    assert registry.async_get("sensor.london_tfl_victoria_940gzzluoxc") is not None
    # Customisations the user made to the strike entities must survive the reload.
    registry.async_update_entity(TODAY, name="Tube strike today", area_id="hallway")
    registry.async_update_entity(SUMMARY, name="Strikes")

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "remove_stop"})
    await hass.config_entries.options.async_configure(result["flow_id"], {"stop_indices": ["1"]})
    await hass.async_block_till_done()

    # The removed stop's three sensors are gone, as before...
    for suffix in ("", "_all", "_scheduled"):
        assert registry.async_get(f"sensor.london_tfl_victoria_940gzzluoxc{suffix}") is None
    # ...the remaining stop and all three strike entities survive the reload.
    assert registry.async_get("sensor.london_tfl_victoria_940gzzluvic") is not None
    for entity_id in (SUMMARY, TODAY, SOON):
        assert registry.async_get(entity_id) is not None
    assert registry.async_get(TODAY).name == "Tube strike today"
    assert registry.async_get(TODAY).area_id == "hallway"
    assert registry.async_get(SUMMARY).name == "Strikes"
