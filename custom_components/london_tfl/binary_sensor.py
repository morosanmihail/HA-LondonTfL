"""Binary sensors: is there a TfL strike today, and is one coming up in the next week."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .strikes import (
    STRIKE_SOON_DAYS,
    describe,
    describe_affected,
    is_strike_soon,
    is_strike_today,
    strike_soon,
    strike_today,
)


async def async_setup_entry(hass, config_entry, async_add_entities):
    config = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = config.get("strike_coordinator")
    entities = []
    if coordinator is not None:
        entities = [
            StrikeTodaySensor(coordinator, config_entry.entry_id),
            StrikeSoonSensor(coordinator, config_entry.entry_id),
        ]

    # Strike alerts turned off: drop the binary sensors left in the registry.
    registry = er.async_get(hass)
    keep = {e.unique_id for e in entities}
    for entry in er.async_entries_for_config_entry(registry, config_entry.entry_id):
        if entry.domain == "binary_sensor" and entry.unique_id not in keep:
            registry.async_remove(entry.entity_id)

    async_add_entities(entities)


class _StrikeBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _unrecorded_attributes = frozenset({"strikes", "affected_now"})

    def __init__(self, coordinator, entry_id: str, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_{key}"

    @property
    def _data(self) -> dict:
        return self.coordinator.data or {}

    @property
    def icon(self):
        return "mdi:account-group" if self.is_on else "mdi:train"


class StrikeTodaySensor(_StrikeBinarySensor):
    """On when a strike affects any watched line for the rest of today: under way, starting later today, or reported
    in TfL's live status."""

    _attr_name = "London TfL strike today"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "strike_today")

    @property
    def is_on(self) -> bool:
        return is_strike_today(self._data, dt_util.now())

    @property
    def extra_state_attributes(self):
        now = dt_util.now()
        return {
            "strikes": [describe(s, now) for s in strike_today(self._data, now)],
            "affected_now": [describe_affected(line) for line in self._data.get("affected_now", [])],
        }


class StrikeSoonSensor(_StrikeBinarySensor):
    """On when a strike on any watched line is under way or starts within the next 7 days."""

    _attr_name = f"London TfL strike within {STRIKE_SOON_DAYS} days"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "strike_soon")

    @property
    def is_on(self) -> bool:
        return is_strike_soon(self._data, dt_util.now())

    @property
    def extra_state_attributes(self):
        now = dt_util.now()
        strikes = strike_soon(self._data, now)
        return {
            "next_strike": describe(strikes[0], now)["when"] if strikes else None,
            "strikes": [describe(s, now) for s in strikes],
        }
