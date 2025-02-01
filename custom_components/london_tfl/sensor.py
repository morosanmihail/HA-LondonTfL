"""Platform for sensor integration."""
from __future__ import annotations

import logging
import json
import uuid
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from datetime import timedelta
from homeassistant import config_entries, core
from homeassistant.components.sensor import SensorEntity, PLATFORM_SCHEMA
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import (
    CONF_STOPS, CONF_LINE, CONF_STATION, CONF_PLATFORM,
    CONF_MAX, DEFAULT_ICONS, DEFAULT_MAX, DEFAULT_NAME, DOMAIN,
    TFL_ARRIVALS_URL, CONF_SHORTEN_STATION_NAMES, get_line_image,
    shortenName, CONF_METHOD, TFL_BUS_ARRIVALS_URL
)
from .network import request
from .tfl_data import TfLData
from .hasl_utils import as_hasl_departures


_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=1)


CONFIG_STOP = vol.Schema({
    vol.Required(CONF_LINE): cv.string,
    vol.Required(CONF_STATION): cv.string,
    vol.Optional(CONF_METHOD, default=''): cv.string,
    vol.Optional(CONF_PLATFORM, default=''): cv.string,
    vol.Optional(CONF_MAX, default=DEFAULT_MAX): cv.positive_int,
    vol.Optional(CONF_SHORTEN_STATION_NAMES, default=False): cv.boolean,
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_STOPS): vol.All(cv.ensure_list, [CONFIG_STOP]),
})


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
            sensors.append(
                LondonTfLSensor(
                    name=name,
                    method=stop[CONF_METHOD] if CONF_METHOD in stop else '',
                    line=stop[CONF_LINE],
                    station=stop[CONF_STATION],
                    platform_filter=stop[CONF_PLATFORM] if CONF_PLATFORM in stop else '',
                    max=stop[CONF_MAX] if CONF_MAX in stop else DEFAULT_MAX,
                    shortenStationNames=(
                        stop[CONF_SHORTEN_STATION_NAMES]
                        if CONF_SHORTEN_STATION_NAMES in stop
                        else False
                    )
                )
            )
    async_add_entities(sensors, update_before_add=True)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None
) -> None:
    """Set up the sensor platform."""
    name = config.get(CONF_NAME)
    stops = config.get(CONF_STOPS)

    sensors = []
    for stop in stops:
        if stop[CONF_STATION] is not None and stop[CONF_LINE] is not None:
            sensors.append(
                LondonTfLSensor(
                    name,
                    stop[CONF_METHOD],
                    stop[CONF_LINE],
                    stop[CONF_STATION],
                    stop[CONF_PLATFORM],
                    stop[CONF_MAX],
                    stop[CONF_SHORTEN_STATION_NAMES],
                )
            )
    async_add_entities(sensors, update_before_add=True)


class LondonTfLSensor(SensorEntity):
    """Representation of a Sensor."""

    def __init__(self, name, method, line, station, platform_filter, max, shortenStationNames):
        """Initialize the sensor."""
        self._platformname = name
        self._name = name + '_' + line + '_' + station
        self.method = method
        self.line = line
        self.station = station
        self.filter_platform = (
            platform_filter.strip() if platform_filter else ''
        )
        self.max_items = int(max)
        self._shorten_station_names = shortenStationNames

        self._state = None
        self._destination = ''
        self._tfl_data = TfLData()

    @property
    def unique_id(self):
        filter_append = '' if not self.filter_platform else ('_' + self.filter_platform)
        return self._platformname + '_' + self.line + '_' + self.station + filter_append

    @property
    def name(self) -> str:
        station = self._tfl_data.get_station_name()
        destination = self._destination
        if self._shorten_station_names:
            station = shortenName(station)
            destination = shortenName(destination)

        if destination and station:
            return "{0} to {1}".format(
                station,
                destination
            )
        if station:
            return "{0} - Idle".format(station)
        return self._name

    def is_not_bus(self) -> bool:
        return (self.method != 'bus')

    @property
    def icon(self):
        """Icon of the sensor."""
        if self.method in DEFAULT_ICONS:
            return DEFAULT_ICONS[self.method]
        return DEFAULT_ICONS['default']

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    async def async_update(self):
        """Fetch new state data for the sensor.
        This is the only method that should fetch new data for Home Assistant.
        """

        url_base = ''
        if self.is_not_bus():
            url_base = TFL_ARRIVALS_URL.format(
                self.line,
                self.station,
                str(uuid.uuid4())
            )
        else:
            url_base = TFL_BUS_ARRIVALS_URL.format(
                self.station,
                str(uuid.uuid4())
            )

        if self._tfl_data.is_data_stale(self.max_items):
            try:
                result = await request(url_base, self)
                if not result:
                    _LOGGER.warning('There was no reply from TfL servers.')
                    self._state = 'Cannot reach TfL'
                    return
                result = json.loads(result)
            except OSError:
                _LOGGER.warning('Something broke.')
                self._state = 'Cannot reach TfL'
                return
            except Exception:
                _LOGGER.warning('Failed to interpret received %s', 'JSON.', exc_info=1)
                self._state = 'Cannot interpret JSON from TfL'
                return
            self._tfl_data.populate(result, self.filter_platform)

        self._tfl_data.sort_data(self.max_items)
        self._state = self._tfl_data.get_state()

    @property
    def extra_state_attributes(self):
        attributes = {}
        attributes['last_refresh'] = self._tfl_data.get_last_update()

        if self._tfl_data.is_empty():
            return attributes

        departures = (
            self._tfl_data.get_departures()
            if self.is_not_bus()
            else self._tfl_data.get_bus_departures()
        )
        attributes['departures'] = as_hasl_departures(departures)

        data = [
            {
                'title_default': 'To $title',
                'line1_default': 'at $time',
                'line2_default': '$studio',
                'line3_default': '',
                'line4_default': '',
                'icon': 'mdi:train',
            }
        ]

        for index, departure in enumerate(departures):
            data.append({
                'title': departure['destination'],
                'airdate': departure['expected'],
                'fanart': get_line_image(self.line),
                'flag': True,
                'studio': departure['platform'],
            })

            if index == 0:
                attributes['expected'] = departure['expected']
                attributes['destination'] = departure['destination']
                attributes['platform'] = departure['platform']
                self._destination = departure['destination']

                attributes['next_departure_minutes'] = departure['time']
                attributes['next_departure_time'] = departure['expected']

        attributes['station_name'] = self._tfl_data.get_station_name()
        attributes['data'] = data

        return attributes
