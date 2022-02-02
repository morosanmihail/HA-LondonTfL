import logging
import json
from typing import Any, Dict, Optional

from homeassistant import config_entries
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .const import (
    CONF_STOPS,
    CONF_STATION,
    CONF_LINE,
    CONF_MAX,
    CONF_PLATFORM,
    DEFAULT_MAX,
    DOMAIN,
    TFL_LINES_URL,
    TFL_STATIONS_URL
)
from .network import request

_LOGGER = logging.getLogger(__name__)


DEFAULT_LINES = {'dlr': 'DLR', 'jubilee': 'Jubilee'}


@config_entries.HANDLERS.register(DOMAIN)
class LondonTfLConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    '''London TfL config flow.'''

    def __init__(self) -> None:
        '''Initialize.'''
        self.data: dict[str, Any] = {
            CONF_STOPS: [],
            'lastLine': ''
        }

    async def async_step_user(
        self,
        user_input: Optional[Dict[str, Any]] = None
    ):
        errors: Dict[str, str] = {}
        if user_input is not None:
            self.data['lastLine'] = user_input[CONF_LINE]

            return await self.async_step_station()

        lines = DEFAULT_LINES
        try:
            result = await request(TFL_LINES_URL, self)
            if not result:
                _LOGGER.warning('There was no reply from TfL servers.')
                errors['base'] = 'request'
            result = json.loads(result)
            lines = {item['id']: item['name'] for item in result}
        except OSError:
            _LOGGER.warning('Something broke.')
            errors['base'] = 'request'

        return self.async_show_form(
            step_id='user',
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LINE): vol.In(lines),
                }
            ),
            errors=errors
        )

    async def async_step_station(
        self,
        user_input: Optional[Dict[str, Any]] = None
    ):
        errors: Dict[str, str] = {}
        if user_input is not None:
            self.data[CONF_STOPS].append(
                {
                    'line': self.data['lastLine'],
                    'station': user_input[CONF_STATION],
                    'max': user_input[CONF_MAX],
                    'platform': user_input[CONF_PLATFORM]
                }
            )
            # If user ticked the box show this form again so they can add an
            # additional station.
            if user_input.get('add_another', False):
                return await self.async_step_user()

            return self.async_create_entry(title='London TfL', data=self.data)

        stations_url = TFL_STATIONS_URL.format(
            self.data['lastLine']
        )

        stations = {}
        try:
            result = await request(stations_url, self)
            if not result:
                _LOGGER.warning('There was no reply from TfL servers.')
                errors['base'] = 'request'
            result = json.loads(result)
            stations = {
                item['stationNaptan']: item['commonName'] for item in result
            }
        except OSError:
            _LOGGER.warning('Something broke.')
            errors['base'] = 'request'

        return self.async_show_form(
            step_id='station',
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STATION): vol.In(stations),
                    vol.Optional(
                        CONF_MAX, default=DEFAULT_MAX
                    ): cv.positive_int,
                    vol.Optional(CONF_PLATFORM, default=''): cv.string,
                    vol.Optional('add_another', default=False): cv.boolean,
                }
            ),
            errors=errors
        )
