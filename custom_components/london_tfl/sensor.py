"""Platform for sensor integration."""

from __future__ import annotations

import logging
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from datetime import timedelta
from typing import Optional
from homeassistant import config_entries, core
from homeassistant.components.sensor import SensorEntity, PLATFORM_SCHEMA
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import (
    CONF_STOPS,
    CONF_LINE,
    CONF_STATION,
    CONF_PLATFORM,
    CONF_MAX,
    CONF_NR_API_KEY,
    DEFAULT_ICONS,
    DEFAULT_MAX,
    DEFAULT_NAME,
    DOMAIN,
    CONF_SHORTEN_STATION_NAMES,
    get_line_image,
    shortenName,
    CONF_METHOD,
)
from .tfl_data import TfLData
from .hasl_utils import as_hasl_departures


_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=1)


CONFIG_STOP = vol.Schema(
    {
        vol.Required(CONF_LINE): cv.string,
        vol.Required(CONF_STATION): cv.string,
        vol.Optional(CONF_METHOD, default=""): cv.string,
        vol.Optional(CONF_PLATFORM, default=""): cv.string,
        vol.Optional(CONF_MAX, default=DEFAULT_MAX): cv.positive_int,
        vol.Optional(CONF_SHORTEN_STATION_NAMES, default=False): cv.boolean,
    }
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Required(CONF_STOPS): vol.All(cv.ensure_list, [CONFIG_STOP]),
    }
)


async def async_setup_entry(
    hass: core.HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities,
):
    """Setup sensors from a config entry created in the integrations UI."""
    config = hass.data[DOMAIN][config_entry.entry_id]

    name = config[CONF_NAME] if CONF_NAME in config else DEFAULT_NAME
    stops = config[CONF_STOPS]

    sensors = []
    for stop in stops:
        if stop[CONF_STATION] is not None and stop[CONF_LINE] is not None:
            shared_data = TfLData(
                method=stop[CONF_METHOD] if CONF_METHOD in stop else "",
                line=stop[CONF_LINE],
                station=stop[CONF_STATION],
                nr_api_key=stop.get(CONF_NR_API_KEY),
            )
            common_kwargs = dict(
                name=name,
                method=stop[CONF_METHOD] if CONF_METHOD in stop else "",
                line=stop[CONF_LINE],
                station=stop[CONF_STATION],
                platform_filter=(
                    stop[CONF_PLATFORM] if CONF_PLATFORM in stop else ""
                ),
                max=stop[CONF_MAX] if CONF_MAX in stop else DEFAULT_MAX,
                shortenStationNames=(
                    stop[CONF_SHORTEN_STATION_NAMES]
                    if CONF_SHORTEN_STATION_NAMES in stop
                    else False
                ),
                nr_api_key=stop.get(CONF_NR_API_KEY),
                tfl_data=shared_data,
            )
            sensors.append(LondonTfLSensor(departure_mode="realtime", **common_kwargs))
            if stop.get(CONF_METHOD) != "national-rail":
                sensors.append(LondonTfLSensor(departure_mode="scheduled", **common_kwargs))
                sensors.append(LondonTfLSensor(departure_mode="all", **common_kwargs))
    # Remove entities from the registry that belong to this config entry but
    # are no longer in the stop list (e.g. after the user removed a stop).
    registry = er.async_get(hass)
    new_unique_ids = {s.unique_id for s in sensors}
    for entry in er.async_entries_for_config_entry(registry, config_entry.entry_id):
        if entry.unique_id not in new_unique_ids:
            registry.async_remove(entry.entity_id)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service("refresh_timetable", {}, "async_force_timetable_refresh")

    async_add_entities(sensors, update_before_add=True)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the sensor platform."""
    name = config.get(CONF_NAME)
    stops = config.get(CONF_STOPS)

    sensors = []
    for stop in stops:
        if stop[CONF_STATION] is not None and stop[CONF_LINE] is not None:
            shared_data = TfLData(
                method=stop[CONF_METHOD],
                line=stop[CONF_LINE],
                station=stop[CONF_STATION],
                nr_api_key=stop.get(CONF_NR_API_KEY),
            )
            common_kwargs = dict(
                name=name,
                method=stop[CONF_METHOD],
                line=stop[CONF_LINE],
                station=stop[CONF_STATION],
                platform_filter=stop[CONF_PLATFORM],
                max=stop[CONF_MAX],
                shortenStationNames=stop[CONF_SHORTEN_STATION_NAMES],
                nr_api_key=stop.get(CONF_NR_API_KEY),
                tfl_data=shared_data,
            )
            sensors.append(LondonTfLSensor(departure_mode="realtime", **common_kwargs))
            if stop[CONF_METHOD] != "national-rail":
                sensors.append(LondonTfLSensor(departure_mode="scheduled", **common_kwargs))
                sensors.append(LondonTfLSensor(departure_mode="all", **common_kwargs))
    async_add_entities(sensors, update_before_add=True)


class LondonTfLSensor(SensorEntity):
    """Representation of a Sensor."""

    def __init__(
        self,
        name,
        method,
        line,
        station,
        platform_filter,
        max,
        shortenStationNames,
        *,
        nr_api_key: Optional[str] = None,
        departure_mode: str = "realtime",
        tfl_data: Optional[TfLData] = None,
    ):
        """Initialize the sensor."""
        self._platformname = name
        mode_suffix = "" if departure_mode == "realtime" else ("_" + departure_mode)
        self._name = name + "_" + line + "_" + station + mode_suffix
        self.entity_id = "sensor." + self._name.lower().replace(" ", "_").replace("-", "_")
        self.method = method
        self.line = line
        self.station = station
        self.filter_platform = platform_filter.strip() if platform_filter else ""
        self.max_items = int(max)
        self._shorten_station_names = shortenStationNames
        self.departure_mode = departure_mode

        self._state = None
        self._destination = ""
        self._departures = []
        self._tfl_data = tfl_data or TfLData(
            method=method, line=line, station=station, nr_api_key=nr_api_key
        )

    @property
    def unique_id(self):
        filter_append = "" if not self.filter_platform else ("_" + self.filter_platform)
        mode_suffix = "" if self.departure_mode == "realtime" else ("_" + self.departure_mode)
        return self._platformname + "_" + self.line + "_" + self.station + filter_append + mode_suffix

    @property
    def name(self) -> str:
        station = self._tfl_data.get_station_name()
        destination = self._destination
        if self._shorten_station_names:
            station = shortenName(station)
            destination = shortenName(destination)

        mode_suffix = {
            "scheduled": " (Scheduled)",
            "all": " (All)",
        }.get(self.departure_mode, "")

        if destination and station:
            return "{0} to {1}{2}".format(station, destination, mode_suffix)
        if station:
            return "{0}{1}".format(station, mode_suffix)
        return self._name + mode_suffix

    @property
    def icon(self):
        """Icon of the sensor."""
        if self.method in DEFAULT_ICONS:
            return DEFAULT_ICONS[self.method]
        return DEFAULT_ICONS["default"]

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    async def async_update(self):
        """Fetch new state data for the sensor.
        This is the only method that should fetch new data for Home Assistant.
        """

        if self._tfl_data.is_data_stale(self.max_items):
            result = await self._tfl_data.fetch(self.hass)
            if isinstance(result, str):
                self._state = result
                return
            self._tfl_data.populate(result, self.filter_platform)

        await self._tfl_data.fetch_timetable(self.hass)

        self._tfl_data.sort_data(self.max_items)
        self._departures = self._tfl_data.get_departures(self.departure_mode)
        self._state = self._tfl_data.get_state_from_departures(self._departures)

    async def async_force_timetable_refresh(self):
        """Force-refresh timetable data. Called via the refresh_timetable service."""
        await self._tfl_data.fetch_timetable(self.hass, force=True)
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        attributes = {}
        attributes["last_refresh"] = self._tfl_data.get_last_update()
        attributes["line_colours"] = self._tfl_data.get_line_colours()

        if not self._departures:
            return attributes

        departures = self._departures
        attributes["departures"] = as_hasl_departures(departures)

        data = [
            {
                "title_default": "To $title",
                "line1_default": "at $time",
                "line2_default": "$studio",
                "line3_default": "",
                "line4_default": "",
                "icon": "mdi:train",
            }
        ]

        for index, departure in enumerate(departures):
            data.append(
                {
                    "title": departure["destination"],
                    "airdate": departure["expected"],
                    "fanart": get_line_image(self.line),
                    "flag": True,
                    "studio": departure["platform"],
                }
            )

            if index == 0:
                attributes["expected"] = departure["expected"]
                attributes["destination"] = departure["destination"]
                attributes["platform"] = departure["platform"]
                self._destination = departure["destination"]

                attributes["next_departure_minutes"] = departure["time"]
                attributes["next_departure_time"] = departure["expected"]

        attributes["station_name"] = self._tfl_data.get_station_name()
        attributes["data"] = data

        return attributes
