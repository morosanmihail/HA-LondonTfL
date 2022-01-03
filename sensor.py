"""Platform for sensor integration."""
from __future__ import annotations

import aiohttp
import async_timeout
import logging
import json
import uuid
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from datetime import timedelta, datetime
from dateutil import parser
from homeassistant.components.sensor import SensorEntity, PLATFORM_SCHEMA
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import  (
    CONF_STOPS,
    CONF_LINE,
    CONF_STATION,
    CONF_PLATFORM,
    CONF_MAX,
    DEFAULT_MAX,
    DEFAULT_NAME,
    LINE_IMAGES,
    DOMAIN
)


_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=1)


CONFIG_STOP = vol.Schema({
    vol.Required(CONF_LINE): cv.string,
    vol.Required(CONF_STATION): cv.string,
    vol.Optional(CONF_PLATFORM, default=''): cv.string,
    vol.Optional(CONF_MAX, default=DEFAULT_MAX): cv.positive_int,
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
        if stop['station'] != None and stop['line'] != None:
            sensors.append(
                LondonTfLSensor(
                    name,
                    stop['line'],
                    stop['station'],
                    stop['platform'] if 'platform' in stop else '',
                    stop['max'] if 'max' in stop else DEFAULT_MAX,
                ))

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
        if stop['station'] != None and stop['line'] != None:
            sensors.append(
                LondonTfLSensor(
                    name,
                    stop['line'],
                    stop['station'],
                    stop['platform'],
                    stop['max'],
                ))
    async_add_entities(sensors, update_before_add=True)


class LondonTfLSensor(SensorEntity):
    """Representation of a Sensor."""

    def __init__(self, name, line, station, platform_filter, max):
        """Initialize the sensor."""
        self._platformname = name
        self._name = name + '_' + line + '_' + station
        self.line = line
        self.station = station
        self.filter_platform = platform_filter.strip() if platform_filter != None else ''
        self.max_items = int(max)

        self._state = None
        self._raw_result = []
        self._api_json = []
        self._destination = ''
        self._last_update = None

    @property
    def unique_id(self):
        return self._platformname + '_' + self.line + '_' + self.station

    @property
    def name(self) -> str:
        return self._destination if self._destination else self._name

    @property
    def icon(self):
        """Icon of the sensor."""
        return "mdi:train"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    async def async_update(self):
        """Fetch new state data for the sensor.
        This is the only method that should fetch new data for Home Assistant.
        """

        need_call = True
        if len(self._raw_result) > 0:
            # check if there are enough already stored to skip a request
            now = datetime.now().timestamp()
            after_now = [
                item for item in self._raw_result
                if parser.parse(item['expectedArrival']).timestamp() > now
            ]

            if len(after_now) >= self.max_items:
                self._raw_result = after_now
                need_call = False

        url_base = 'https://api.tfl.gov.uk/line/{0}/arrivals/{1}?test={2}'.format(
            self.line,
            self.station,
            str(uuid.uuid4())
        )

        if need_call:
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

            if self.filter_platform != '':
                self._raw_result = [item for item in result if item['platformName'] == self.filter_platform]
            else:
                self._raw_result = result

            self._last_update = datetime.now()

        self._api_json = sorted(
            self._raw_result, key=lambda i: i['expectedArrival'], reverse=False
        )[:self.max_items]

        if len(self._api_json) > 0:
            self._state = parser.parse(self._api_json[0]['expectedArrival']).strftime('%H:%M')
        else:
            self._state = 'None'

    @property
    def extra_state_attributes(self):
        attributes = {}

        attributes['LastUpdate'] = self._last_update

        if len(self._api_json) == 0:
            return attributes

        data = [
            {
                'title_default': 'To $title',
                'line1_default': 'at $time',
                'line2_default': '',
                'line3_default': '',
                'line4_default': '',
                'icon': 'mdi:train',
            }
        ]

        index = 0
        for item in self._api_json:
            departure = {}
            departure['Destination'] = item['destinationName']
            exp_time = parser.parse(item['expectedArrival']).strftime('%H:%M')
            departure['ExpectedTime'] = exp_time
            departure['TimeToStation'] = time_to_station(item, False)
            attributes['{0}'.format(index)] = departure

            data.append({
                'title': departure['Destination'],
                'airdate': item['expectedArrival'],
                'fanart': LINE_IMAGES[self.line] if self.line in LINE_IMAGES else LINE_IMAGES['default'],
                'flag': True,
            })

            if index == 0:
                attributes['remaining'] = time_to_station(self._api_json[0], False, '0:{0}:{1}')
                attributes['expected'] = exp_time
                attributes['destination'] = item['destinationName']
                self._destination = item['destinationName']

            index = index + 1

        attributes['data'] = data

        return attributes


def time_to_station(entry, with_destination = True, style = '{0}m {1}s'):
    next_departure_time = entry['timeToStation']
    next_departure_dest = entry['destinationName']
    return style.format(
        int(next_departure_time / 60),
        int(next_departure_time % 60)
    ) + (' to ' + next_departure_dest if with_destination else '')


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
