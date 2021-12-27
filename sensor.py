"""Platform for sensor integration."""
from __future__ import annotations
from time import time

import aiohttp
import async_timeout
import logging
import json
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from datetime import datetime, timedelta
from dateutil import parser
from homeassistant.components.sensor import SensorEntity, PLATFORM_SCHEMA
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

SCAN_INTERVAL = timedelta(minutes=3)
_LOGGER = logging.getLogger(__name__)


async def fetch(session, url):
    try:
        with async_timeout.timeout(2):
            async with session.get(
                url, headers={
                    "Accept": "application/json"
                }
            ) as response:
                return await response.text()
    except:
        pass


async def request(url, self):
    async with aiohttp.ClientSession() as session:
        return await fetch(session, url)

        
DEFAULT_NAME = 'London TfL'
CONF_LINE = 'line'
CONF_STATION = 'station'
CONF_PLATFORM = 'platform_filter'
CONF_MAX = 'max'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_LINE): cv.string,
    vol.Required(CONF_STATION): cv.string,
    vol.Optional(CONF_PLATFORM, default=''): cv.string,
    vol.Optional(CONF_MAX, default=3): cv.positive_int,
})

def setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None
) -> None:
    """Set up the sensor platform."""
    name = config.get(CONF_NAME)
    add_entities([LondonTfLSensor(hass, config, name)])


class LondonTfLSensor(SensorEntity):
    """Representation of a Sensor."""

    def __init__(self, hass, conf, name):
        """Initialize the sensor."""
        self._name = name
        self._state = None
        self._api_json = []
        self.line = conf.get(CONF_LINE)
        self.station = conf.get(CONF_STATION)
        self.filter_platform = conf.get(CONF_PLATFORM)
        self.max_items = int(conf.get(CONF_MAX))

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    async def async_update(self):
        """Fetch new state data for the sensor.
        This is the only method that should fetch new data for Home Assistant.
        """

        url_base = 'https://api.tfl.gov.uk/line/{0}/arrivals/{1}'.format(
            self.line,
            self.station
        )

        try:
            result = await request(url_base, self)
            if not result:
                self._state = 'Cannot reach TfL'
                return
            result = json.loads(result)
        except OSError:
            self._state = 'Cannot reach TfL'
            return

        self._api_json = result
        if self.filter_platform != '':
            self._api_json = [item for item in self._api_json if item['platformName'] == self.filter_platform]
        self._api_json = sorted(
            self._api_json, key=lambda i: i['expectedArrival'], reverse=False
        )[:self.max_items]

        self._state = time_to_station(self._api_json[0])

    @property
    def extra_state_attributes(self):
        attributes = {}

        index = 0
        for item in self._api_json:
            departure = {}
            departure['Destination'] = item['destinationName']
            exp_time = parser.parse(item['expectedArrival'])
            departure['ExpectedTime'] = exp_time.strftime('%H:%M')
            departure['TimeToStation'] = time_to_station(item, False)
            attributes['{0}'.format(index)] = departure
            index = index + 1

        return attributes


def time_to_station(entry, with_destination = True):
    next_departure_time = entry['timeToStation']
    next_departure_dest = entry['destinationName']
    return '{0}m {1}s'.format(
        int(next_departure_time / 60),
        int(next_departure_time % 60)
    ) + (' to ' + next_departure_dest if with_destination else '')