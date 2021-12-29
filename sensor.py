"""Platform for sensor integration."""
from __future__ import annotations
from time import time
from typing import OrderedDict

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

SCAN_INTERVAL = timedelta(minutes=2)
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
CONF_STOPS = 'stops'

CONF_LINE = 'line'
CONF_STATION = 'station'
CONF_PLATFORM = 'platform'
CONF_MAX = 'max'

CONFIG_STOP = vol.Schema({
    vol.Required(CONF_LINE): cv.string,
    vol.Required(CONF_STATION): cv.string,
    vol.Optional(CONF_PLATFORM, default=''): cv.string,
    vol.Optional(CONF_MAX, default=3): cv.positive_int,
})

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_STOPS): vol.All(cv.ensure_list, [CONFIG_STOP]), 
})

def setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None
) -> None:
    """Set up the sensor platform."""
    name = config.get(CONF_NAME)
    stops = config.get(CONF_STOPS)

    for stop in stops:
        if stop['station'] != None and stop['line'] != None:
            add_entities(
                [LondonTfLSensor(
                    name + '_' + stop['line'] + '_' + stop['station'], 
                    stop['line'], 
                    stop['station'], 
                    stop['platform'],
                    stop['max'],
                )]
        )


class LondonTfLSensor(SensorEntity):
    """Representation of a Sensor."""

    def __init__(self, name, line, station, platform_filter, max):
        """Initialize the sensor."""
        self._name = name
        self.line = line
        self.station = station
        self.filter_platform = platform_filter.strip() if platform_filter != None else ''
        self.max_items = int(max)

        self._state = None
        self._api_json = []
        self._destination = ''

    @property
    def name(self) -> str:
        return self._destination if self._destination else self._name

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

        if len(self._api_json) > 0:
            self._state = parser.parse(self._api_json[0]['expectedArrival']).strftime('%H:%M')
        else:
            self._state = 'None'

    @property
    def extra_state_attributes(self):
        attributes = {}

        if len(self._api_json) == 0:
            return attributes 

        index = 0
        for item in self._api_json:
            departure = {}
            departure['Destination'] = item['destinationName']
            exp_time = parser.parse(item['expectedArrival']).strftime('%H:%M')
            departure['ExpectedTime'] = exp_time
            departure['TimeToStation'] = time_to_station(item, False)
            attributes['{0}'.format(index)] = departure

            if index == 0:
                attributes['remaining'] = time_to_station(self._api_json[0], False, '0:{0}:{1}')
                attributes['expected'] = exp_time
                attributes['destination'] = item['destinationName']
                self._destination = item['destinationName']

            index = index + 1

        return attributes


def time_to_station(entry, with_destination = True, style = '{0}m {1}s'):
    next_departure_time = entry['timeToStation']
    next_departure_dest = entry['destinationName']
    return style.format(
        int(next_departure_time / 60),
        int(next_departure_time % 60)
    ) + (' to ' + next_departure_dest if with_destination else '')