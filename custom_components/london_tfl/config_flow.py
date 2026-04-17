import logging
import json
from typing import Any

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .const import (
    CONF_STOPS,
    CONF_STATION,
    CONF_LINE,
    CONF_METHOD,
    CONF_SHORTEN_STATION_NAMES,
    CONF_MAX,
    CONF_NR_API_KEY,
    CONF_PLATFORM,
    DEFAULT_MAX,
    DEFAULT_METHODS,
    DOMAIN,
    TFL_LINES_URL,
    TFL_STATIONS_URL,
)
from .network import request

_LOGGER = logging.getLogger(__name__)


@config_entries.HANDLERS.register(DOMAIN)
class LondonTfLConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """London TfL config flow."""

    def __init__(self) -> None:
        """Initialize."""
        self.data: dict[str, Any] = {
            CONF_STOPS: [],
            "lastLine": "",
            "lastMethod": "",
        }

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "OptionsFlowHandler":
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self.data["lastMethod"] = user_input[CONF_METHOD]
            return await self.async_step_lines()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_METHOD): vol.In(DEFAULT_METHODS),
                }
            ),
            errors=errors,
        )

    async def async_step_lines(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self.data["lastLine"] = user_input[CONF_LINE]
            return await self.async_step_station()

        lines = {}
        try:
            url_base = TFL_LINES_URL.format(self.data["lastMethod"])
            result = await request(url_base)
            if not result:
                _LOGGER.warning("There was no reply from TfL servers.")
            else:
                lines = {item["id"]: item["name"] for item in json.loads(result)}
        except Exception:
            _LOGGER.warning("Failed to fetch lines", exc_info=True)

        if not lines:
            return self.async_abort(reason="cannot_connect")

        return self.async_show_form(
            step_id="lines",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LINE): vol.In(lines),
                }
            ),
            errors=errors,
        )

    async def async_step_station(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self.data[CONF_STOPS].append(
                {
                    CONF_LINE: self.data["lastLine"],
                    CONF_METHOD: self.data["lastMethod"],
                    CONF_NR_API_KEY: user_input.get(CONF_NR_API_KEY, None),
                    CONF_STATION: user_input[CONF_STATION],
                    CONF_MAX: user_input[CONF_MAX],
                    CONF_PLATFORM: user_input[CONF_PLATFORM],
                    CONF_SHORTEN_STATION_NAMES: user_input[CONF_SHORTEN_STATION_NAMES],
                }
            )
            if user_input.get("add_another", False):
                return await self.async_step_user()

            return self.async_create_entry(
                title=user_input[CONF_STATION], data=self.data
            )

        stations_url = TFL_STATIONS_URL.format(self.data["lastLine"])

        stations = {}
        try:
            result = await request(stations_url)
            if not result:
                _LOGGER.warning("There was no reply from TfL servers.")
            else:
                data = json.loads(result)
                if self.data["lastMethod"] != "bus":
                    stations = {item["stationNaptan"]: item["commonName"] for item in data}
                else:
                    stations = {item["id"]: item["commonName"] for item in data}
        except Exception:
            _LOGGER.warning("Failed to fetch stations", exc_info=True)

        if not stations:
            return self.async_abort(reason="cannot_connect")

        description_placeholders = {"extra_description": ""}
        extra_fields = {}

        if (
            self.data["lastMethod"] == "national-rail"
            and self.data["lastLine"] != "thameslink"
        ):
            # if a user already supplied a token in this specific flow, retrieve it
            kwargs = {}
            for stop in self.data[CONF_STOPS]:
                token = stop.get(CONF_NR_API_KEY)
                if token is not None:
                    kwargs["default"] = token

            extra_fields[vol.Required(CONF_NR_API_KEY, **kwargs)] = cv.string
            description_placeholders["extra_description"] = """
This line is not supported by the TfL API and requires an extra API token in order to see departure times.
Please register at https://realtime.nationalrail.co.uk/OpenLDBWSRegistration/Registration and provide the token here.
If you are already registered, you can reuse the same token as many times as you want.
"""

        return self.async_show_form(
            step_id="station",
            data_schema=vol.Schema(
                {
                    **extra_fields,
                    vol.Required(CONF_STATION): vol.In(stations),
                    vol.Optional(CONF_SHORTEN_STATION_NAMES, default=False): cv.boolean,
                    vol.Optional(CONF_MAX, default=DEFAULT_MAX): cv.positive_int,
                    vol.Optional(CONF_PLATFORM, default=""): cv.string,
                    vol.Optional("add_another", default=False): cv.boolean,
                }
            ),
            errors=errors,
            description_placeholders=description_placeholders,
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow for managing monitored stops."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize, loading current stops from options (if previously edited) or data."""
        self._config_entry = config_entry
        # Options override data once the options flow has been used at least once.
        self._stops: list[dict[str, Any]] = list(
            config_entry.options.get(CONF_STOPS)
            or config_entry.data.get(CONF_STOPS, [])
        )
        self._last_method: str = ""
        self._last_line: str = ""
        self._editing_index: int | None = None
        # Cached station name map populated when showing the add-station form.
        self._current_stations: dict[str, str] = {}

    def _stop_label(self, stop: dict[str, Any]) -> str:
        """Return a human-readable label for a stop."""
        station_id = stop.get(CONF_STATION, "?")
        name = stop.get("station_display_name") or station_id
        return f"{stop.get(CONF_METHOD, '?')} / {stop.get(CONF_LINE, '?')} / {name}"

    def _save(self):
        """Persist the current stops list and close the options flow."""
        return self.async_create_entry(title="", data={CONF_STOPS: self._stops})

    # ── Entry point (menu) ────────────────────────────────────────────────────

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Show the top-level menu, hiding edit/remove when no stops exist."""
        menu_options: list[str] = ["add_stop"]
        if self._stops:
            menu_options += ["edit_stop", "remove_stop"]
        return self.async_show_menu(step_id="init", menu_options=menu_options)

    # ── Add stop (method → line → station) ───────────────────────────────────

    async def async_step_add_stop(self, user_input: dict[str, Any] | None = None):
        """Step 1 of adding a stop: pick the transport method."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._last_method = user_input[CONF_METHOD]
            return await self.async_step_add_line()

        return self.async_show_form(
            step_id="add_stop",
            data_schema=vol.Schema(
                {vol.Required(CONF_METHOD): vol.In(DEFAULT_METHODS)}
            ),
            errors=errors,
        )

    async def async_step_add_line(self, user_input: dict[str, Any] | None = None):
        """Step 2 of adding a stop: pick the line."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._last_line = user_input[CONF_LINE]
            return await self.async_step_add_station()

        lines = {}
        try:
            result = await request(TFL_LINES_URL.format(self._last_method))
            if not result:
                _LOGGER.warning(
                    "No reply from TfL when fetching lines for method %s",
                    self._last_method,
                )
            else:
                lines = {item["id"]: item["name"] for item in json.loads(result)}
        except Exception:
            _LOGGER.warning(
                "Failed to fetch lines for method %s", self._last_method, exc_info=True
            )

        if not lines:
            return self.async_abort(reason="cannot_connect")

        return self.async_show_form(
            step_id="add_line",
            data_schema=vol.Schema({vol.Required(CONF_LINE): vol.In(lines)}),
            errors=errors,
        )

    async def async_step_add_station(self, user_input: dict[str, Any] | None = None):
        """Step 3 of adding a stop: pick the station and set options."""
        if user_input is not None:
            self._stops.append(
                {
                    CONF_LINE: self._last_line,
                    CONF_METHOD: self._last_method,
                    CONF_NR_API_KEY: user_input.get(CONF_NR_API_KEY),
                    CONF_STATION: user_input[CONF_STATION],
                    CONF_MAX: user_input[CONF_MAX],
                    CONF_PLATFORM: user_input[CONF_PLATFORM],
                    CONF_SHORTEN_STATION_NAMES: user_input[CONF_SHORTEN_STATION_NAMES],
                    # Store display name so the edit/remove UI shows it without an API call.
                    "station_display_name": self._current_stations.get(
                        user_input[CONF_STATION], ""
                    ),
                }
            )
            return self._save()

        errors: dict[str, str] = {}
        self._current_stations = {}
        try:
            result = await request(TFL_STATIONS_URL.format(self._last_line))
            if not result:
                _LOGGER.warning(
                    "No reply from TfL when fetching stations for line %s",
                    self._last_line,
                )
            else:
                data = json.loads(result)
                if self._last_method != "bus":
                    self._current_stations = {
                        item["stationNaptan"]: item["commonName"] for item in data
                    }
                else:
                    self._current_stations = {
                        item["id"]: item["commonName"] for item in data
                    }
        except Exception:
            _LOGGER.warning(
                "Failed to fetch stations for line %s", self._last_line, exc_info=True
            )

        if not self._current_stations:
            return self.async_abort(reason="cannot_connect")

        extra_fields: dict = {}
        description_placeholders = {"extra_description": ""}
        if self._last_method == "national-rail" and self._last_line != "thameslink":
            # Reuse a token already entered for another NR stop in this session.
            kwargs: dict = {}
            for stop in self._stops:
                if stop.get(CONF_NR_API_KEY):
                    kwargs["default"] = stop[CONF_NR_API_KEY]
                    break
            extra_fields[vol.Required(CONF_NR_API_KEY, **kwargs)] = cv.string
            description_placeholders["extra_description"] = """
This line is not supported by the TfL API and requires an extra API token in order to see departure times.
Please register at https://realtime.nationalrail.co.uk/OpenLDBWSRegistration/Registration and provide the token here.
"""

        return self.async_show_form(
            step_id="add_station",
            data_schema=vol.Schema(
                {
                    **extra_fields,
                    vol.Required(CONF_STATION): vol.In(self._current_stations),
                    vol.Optional(CONF_SHORTEN_STATION_NAMES, default=False): cv.boolean,
                    vol.Optional(CONF_MAX, default=DEFAULT_MAX): cv.positive_int,
                    vol.Optional(CONF_PLATFORM, default=""): cv.string,
                }
            ),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    # ── Edit stop ─────────────────────────────────────────────────────────────

    async def async_step_edit_stop(self, user_input: dict[str, Any] | None = None):
        """Select which stop to edit."""
        if not self._stops:
            return await self.async_step_init()

        if user_input is not None:
            self._editing_index = int(user_input["stop_index"])
            return await self.async_step_edit_station()

        stop_choices = {str(i): self._stop_label(s) for i, s in enumerate(self._stops)}
        return self.async_show_form(
            step_id="edit_stop",
            data_schema=vol.Schema({vol.Required("stop_index"): vol.In(stop_choices)}),
        )

    async def async_step_edit_station(self, user_input: dict[str, Any] | None = None):
        """Edit the options for the selected stop (max, platform, shorten names, NR token)."""
        stop = self._stops[self._editing_index]

        if user_input is not None:
            self._stops[self._editing_index] = {
                **stop,
                CONF_NR_API_KEY: user_input.get(
                    CONF_NR_API_KEY, stop.get(CONF_NR_API_KEY)
                ),
                CONF_MAX: user_input[CONF_MAX],
                CONF_PLATFORM: user_input[CONF_PLATFORM],
                CONF_SHORTEN_STATION_NAMES: user_input[CONF_SHORTEN_STATION_NAMES],
            }
            return self._save()

        extra_fields: dict = {}
        if (
            stop.get(CONF_METHOD) == "national-rail"
            and stop.get(CONF_LINE) != "thameslink"
        ):
            kwargs: dict = {}
            if stop.get(CONF_NR_API_KEY):
                kwargs["default"] = stop[CONF_NR_API_KEY]
            extra_fields[vol.Optional(CONF_NR_API_KEY, **kwargs)] = cv.string

        return self.async_show_form(
            step_id="edit_station",
            description_placeholders={"stop_name": self._stop_label(stop)},
            data_schema=vol.Schema(
                {
                    **extra_fields,
                    vol.Optional(
                        CONF_SHORTEN_STATION_NAMES,
                        default=stop.get(CONF_SHORTEN_STATION_NAMES, False),
                    ): cv.boolean,
                    vol.Optional(
                        CONF_MAX, default=stop.get(CONF_MAX, DEFAULT_MAX)
                    ): cv.positive_int,
                    vol.Optional(
                        CONF_PLATFORM, default=stop.get(CONF_PLATFORM, "")
                    ): cv.string,
                }
            ),
        )

    # ── Remove stop ───────────────────────────────────────────────────────────

    async def async_step_remove_stop(self, user_input: dict[str, Any] | None = None):
        """Select one or more stops to remove."""
        if not self._stops:
            return await self.async_step_init()

        if user_input is not None:
            indices_to_remove = {int(i) for i in user_input["stop_indices"]}
            self._stops = [
                s for i, s in enumerate(self._stops) if i not in indices_to_remove
            ]
            return self._save()

        stop_options = [
            selector.SelectOptionDict(value=str(i), label=self._stop_label(s))
            for i, s in enumerate(self._stops)
        ]
        return self.async_show_form(
            step_id="remove_stop",
            data_schema=vol.Schema(
                {
                    vol.Required("stop_indices"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=stop_options, multiple=True
                        )
                    ),
                }
            ),
        )

