from copy import deepcopy
import logging
from typing import Any, Dict, Optional

from homeassistant import config_entries, core
from homeassistant.const import CONF_NAME, CONF_PATH
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_registry import (
    async_entries_for_config_entry,
    async_get_registry,
)
import voluptuous as vol

from .const import (
    CONF_STOPS,
    CONF_STATION,
    CONF_LINE,
    CONF_MAX,
    CONF_PLATFORM,
    DOMAIN
)

_LOGGER = logging.getLogger(__name__)


DEFAULT_LINES = ['dlr', 'jubilee']

STOP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_LINE): vol.In(DEFAULT_LINES),
        vol.Required(CONF_STATION): cv.string,
        vol.Optional(CONF_MAX): cv.positive_int,
        vol.Optional(CONF_PLATFORM): cv.string,
        vol.Optional("add_another"): cv.boolean,
    }
)


@config_entries.HANDLERS.register(DOMAIN)
class LondonTfLConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """London TfL config flow."""

    def __init__(self) -> None:
        """Initialize."""
        self.data: dict[str, Any] = {
            CONF_STOPS: []
        }

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}
        if user_input is not None:
            self.data[CONF_STOPS].append(
                {
                    "line": user_input[CONF_LINE],
                    "station": user_input[CONF_STATION],
                    "max": user_input[CONF_MAX],
                    "platform": user_input[CONF_PLATFORM]
                }
            )
            # If user ticked the box show this form again so they can add an
            # additional station.
            if user_input.get("add_another", False):
                return await self.async_step_user()

            return self.async_create_entry(title="London TfL", data=self.data)

        return self.async_show_form(
            step_id="user", data_schema=STOP_SCHEMA, errors=errors
        )


# TODO:
# Split into 2 steps: 1 pick line, 2 use https://api.tfl.gov.uk/line/jubilee/stoppoints to get stops
