import logging
import json
from typing import Any, NamedTuple

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
    CONF_RDM_API_KEY,
    CONF_CRS,
    CONF_PLATFORM,
    CRS_LOOKUP_URL,
    DEFAULT_MAX,
    DEFAULT_METHODS,
    DOMAIN,
    NR_LEGACY_REGISTRATION_URL,
    NR_REGISTRATION_URL,
    TFL_LINES_URL,
    TFL_STATIONS_URL,
)
from .codes import atco_to_crs, is_valid_crs, load_crs_codes
from .network import request

_LOGGER = logging.getLogger(__name__)


class StationOption(NamedTuple):
    """A station-picker entry.

    label: rich text for the dropdown (name + indicator + towards + line coverage).
    name: plain station name, no suffixes.
    display_name: name + "(towards ...)" when TfL provides a direction — this is
        what gets persisted as station_display_name / used for the entry title,
        since direction is a stable physical property of the stop (unlike line
        coverage, which depends on what was selected in this particular flow).
    crs: National Rail CRS code, when known (national-rail stations only).
    """

    label: str
    name: str
    display_name: str
    crs: str = ""


_DESCRIPTION_PLACEHOLDERS = {
    "registration_url": NR_REGISTRATION_URL,
    "legacy_registration_url": NR_LEGACY_REGISTRATION_URL,
    "crs_lookup_url": CRS_LOOKUP_URL,
}


def _needs_nr_keys(method: str, line_csv: str) -> bool:
    """National Rail stops need LDBWS credentials.

    The only exception is a stop covering Thameslink alone, which TfL's own
    arrivals API serves. Any other selection, including Thameslink mixed with
    other lines, is fetched from LDBWS (see TfLData._method_property).
    """
    return method == "national-rail" and line_csv != "thameslink"


def _nr_fields(stops: list[dict[str, Any]]) -> dict:
    """Form fields for National Rail credentials and CRS override.

    API keys default to ones already entered for another stop in this flow.
    """
    defaults: dict[str, str] = {}
    for stop in stops:
        for key in (CONF_RDM_API_KEY, CONF_NR_API_KEY):
            if stop.get(key) and key not in defaults:
                defaults[key] = stop[key]

    fields: dict = {}
    for key in (CONF_RDM_API_KEY, CONF_NR_API_KEY):
        kwargs = {"default": defaults[key]} if key in defaults else {}
        fields[vol.Optional(key, **kwargs)] = cv.string
    fields[vol.Optional(CONF_CRS)] = cv.string
    return fields


async def _resolve_nr_stop(
    hass, user_input: dict[str, Any], station_id: str, option: "StationOption | None"
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate National Rail credentials and resolve the station's CRS code.

    Returns (fields to store on the stop, form errors).
    """
    errors: dict[str, str] = {}
    rdm_key = (user_input.get(CONF_RDM_API_KEY) or "").strip() or None
    nr_key = (user_input.get(CONF_NR_API_KEY) or "").strip() or None
    if not rdm_key and not nr_key:
        errors["base"] = "api_key_required"

    crs = (user_input.get(CONF_CRS) or "").strip().upper()
    if crs:
        if not is_valid_crs(crs):
            errors[CONF_CRS] = "invalid_crs"
    elif option is not None and option.crs:
        crs = option.crs
    else:
        try:
            crs = await atco_to_crs(hass, station_id, option.name if option else "")
        except Exception:
            _LOGGER.warning("Could not resolve CRS code for %s", station_id, exc_info=True)
            errors[CONF_CRS] = "crs_not_found"

    return {CONF_RDM_API_KEY: rdm_key, CONF_NR_API_KEY: nr_key, CONF_CRS: crs or None}, errors


def _direction_text(item: dict) -> str:
    """Extract a human direction hint ("towards X" or a compass point) if present.

    Only populated by TfL for stop-level (e.g. bus) stoppoints, not for
    station-level ones (e.g. tube), so this is often "".
    """
    towards = ""
    compass = ""
    for prop in item.get("additionalProperties", []):
        if prop.get("category") != "Direction":
            continue
        if prop.get("key") == "Towards":
            towards = prop.get("value", "")
        elif prop.get("key") == "CompassPoint":
            compass = prop.get("value", "")
    return towards or compass


async def _fetch_stations(method: str, line_csv: str) -> dict[str, StationOption]:
    """Fetch and merge stoppoints for one or more comma-separated line ids.

    The TfL StopPoints endpoint does not accept a comma-separated line list
    (unlike the arrivals endpoint), so each line id is queried separately
    and the results are merged (union, not intersection) so a station only
    served by some of the selected lines is still selectable.

    When multiple lines are selected, stations served by more of them are
    sorted first and their dropdown label notes how many ("2/3 lines"),
    since those are the stations most likely to be what the user actually
    wants (all selected lines returning arrivals from a single stop). The
    label also includes the stop indicator and direction ("towards ...")
    when TfL provides them (bus stops; not present for station-level modes
    like tube/DLR).
    """
    line_ids = line_csv.split(",")
    items: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for line_id in line_ids:
        try:
            result = await request(TFL_STATIONS_URL.format(line_id))
            if not result:
                _LOGGER.warning(
                    "No reply from TfL when fetching stations for line %s", line_id
                )
                continue
            data = json.loads(result)
            id_key = "id" if method == "bus" else "stationNaptan"
            for item in data:
                station_id = item[id_key]
                items[station_id] = item
                counts[station_id] = counts.get(station_id, 0) + 1
        except Exception:
            _LOGGER.warning(
                "Failed to fetch stations for line %s", line_id, exc_info=True
            )

    total_lines = len(line_ids)
    ordered_ids = sorted(items, key=lambda sid: counts[sid], reverse=True)

    crs_codes = await load_crs_codes() if method == "national-rail" and items else None

    stations: dict[str, StationOption] = {}
    for station_id in ordered_ids:
        item = items[station_id]
        name = item["commonName"]

        direction = _direction_text(item)
        display_name = f"{name} (towards {direction})" if direction else name

        label = name
        if item.get("indicator"):
            label += f" ({item['indicator']})"
        if direction:
            label += f" — towards {direction}"
        crs = (crs_codes.lookup(station_id, name) or "") if crs_codes else ""
        if crs:
            label += f" [{crs}]"
        if total_lines > 1:
            label += f" [{counts[station_id]}/{total_lines} lines]"

        stations[station_id] = StationOption(
            label=label, name=name, display_name=display_name, crs=crs
        )

    return stations


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
        # Cached station lookup from the last time the station form was shown,
        # used to resolve a friendly name on submit (kept off self.data since
        # that dict is persisted verbatim as the config entry's data).
        self._current_stations: dict[str, StationOption] = {}

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
            self.data["lastLine"] = ",".join(user_input[CONF_LINE])
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

        line_options = [
            selector.SelectOptionDict(value=line_id, label=name)
            for line_id, name in lines.items()
        ]
        return self.async_show_form(
            step_id="lines",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LINE): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=line_options, multiple=True
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_station(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        needs_nr_keys = _needs_nr_keys(self.data["lastMethod"], self.data["lastLine"])
        if user_input is not None:
            station_id = user_input[CONF_STATION]
            station_option = self._current_stations.get(station_id)
            nr_data: dict[str, Any] = {CONF_NR_API_KEY: None}
            if needs_nr_keys:
                nr_data, errors = await _resolve_nr_stop(
                    self.hass, user_input, station_id, station_option
                )

        if user_input is not None and not errors:
            self.data[CONF_STOPS].append(
                {
                    CONF_LINE: self.data["lastLine"],
                    CONF_METHOD: self.data["lastMethod"],
                    **nr_data,
                    CONF_STATION: station_id,
                    CONF_MAX: user_input[CONF_MAX],
                    CONF_PLATFORM: user_input[CONF_PLATFORM],
                    CONF_SHORTEN_STATION_NAMES: user_input[CONF_SHORTEN_STATION_NAMES],
                    # Store display name so it survives without a fresh API call
                    # (used for both the entry title below and the options-flow
                    # edit/remove menu's stop labels).
                    "station_display_name": station_option.display_name if station_option else station_id,
                }
            )
            if user_input.get("add_another", False):
                return await self.async_step_user()

            title = ", ".join(
                stop.get("station_display_name") or stop[CONF_STATION]
                for stop in self.data[CONF_STOPS]
            )
            return self.async_create_entry(title=title, data=self.data)

        # Reuse the station list when re-showing the form after a validation error.
        if user_input is None or not self._current_stations:
            self._current_stations = await _fetch_stations(
                self.data["lastMethod"], self.data["lastLine"]
            )
        stations = self._current_stations

        if not stations:
            return self.async_abort(reason="cannot_connect")

        extra_fields = _nr_fields(self.data[CONF_STOPS]) if needs_nr_keys else {}

        data_schema = vol.Schema(
            {
                **extra_fields,
                vol.Required(CONF_STATION): vol.In(
                    {sid: opt.label for sid, opt in stations.items()}
                ),
                vol.Optional(CONF_SHORTEN_STATION_NAMES, default=False): cv.boolean,
                vol.Optional(CONF_MAX, default=DEFAULT_MAX): cv.positive_int,
                vol.Optional(CONF_PLATFORM, default=""): cv.string,
                vol.Optional("add_another", default=False): cv.boolean,
            }
        )
        if user_input is not None:
            data_schema = self.add_suggested_values_to_schema(data_schema, user_input)

        return self.async_show_form(
            step_id="station",
            data_schema=data_schema,
            errors=errors,
            description_placeholders=_DESCRIPTION_PLACEHOLDERS,
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
        # Cached station map populated when showing the add-station form.
        self._current_stations: dict[str, StationOption] = {}

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
            self._last_line = ",".join(user_input[CONF_LINE])
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

        line_options = [
            selector.SelectOptionDict(value=line_id, label=name)
            for line_id, name in lines.items()
        ]
        return self.async_show_form(
            step_id="add_line",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LINE): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=line_options, multiple=True
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_add_station(self, user_input: dict[str, Any] | None = None):
        """Step 3 of adding a stop: pick the station and set options."""
        errors: dict[str, str] = {}
        needs_nr_keys = _needs_nr_keys(self._last_method, self._last_line)
        if user_input is not None:
            nr_data: dict[str, Any] = {CONF_NR_API_KEY: None}
            if needs_nr_keys:
                nr_data, errors = await _resolve_nr_stop(
                    self.hass,
                    user_input,
                    user_input[CONF_STATION],
                    self._current_stations.get(user_input[CONF_STATION]),
                )

        if user_input is not None and not errors:
            self._stops.append(
                {
                    CONF_LINE: self._last_line,
                    CONF_METHOD: self._last_method,
                    **nr_data,
                    CONF_STATION: user_input[CONF_STATION],
                    CONF_MAX: user_input[CONF_MAX],
                    CONF_PLATFORM: user_input[CONF_PLATFORM],
                    CONF_SHORTEN_STATION_NAMES: user_input[CONF_SHORTEN_STATION_NAMES],
                    # Store display name so the edit/remove UI shows it without an API call.
                    "station_display_name": (
                        self._current_stations[user_input[CONF_STATION]].display_name
                        if user_input[CONF_STATION] in self._current_stations
                        else ""
                    ),
                }
            )
            return self._save()

        # Reuse the station list when re-showing the form after a validation error.
        if user_input is None or not self._current_stations:
            self._current_stations = await _fetch_stations(
                self._last_method, self._last_line
            )

        if not self._current_stations:
            return self.async_abort(reason="cannot_connect")

        # Reuse keys already entered for another NR stop in this entry.
        extra_fields = _nr_fields(self._stops) if needs_nr_keys else {}

        data_schema = vol.Schema(
            {
                **extra_fields,
                vol.Required(CONF_STATION): vol.In(
                    {sid: opt.label for sid, opt in self._current_stations.items()}
                ),
                vol.Optional(CONF_SHORTEN_STATION_NAMES, default=False): cv.boolean,
                vol.Optional(CONF_MAX, default=DEFAULT_MAX): cv.positive_int,
                vol.Optional(CONF_PLATFORM, default=""): cv.string,
            }
        )
        if user_input is not None:
            data_schema = self.add_suggested_values_to_schema(data_schema, user_input)

        return self.async_show_form(
            step_id="add_station",
            data_schema=data_schema,
            errors=errors,
            description_placeholders=_DESCRIPTION_PLACEHOLDERS,
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
        needs_nr_keys = _needs_nr_keys(
            stop.get(CONF_METHOD, ""), stop.get(CONF_LINE, "")
        )
        errors: dict[str, str] = {}

        if user_input is not None:
            nr_data: dict[str, Any] = {}
            if needs_nr_keys:
                # Fields use suggested values (not defaults) so they can be cleared,
                # e.g. to drop a legacy Darwin token after switching to Rail Data,
                # or to re-detect the CRS code by blanking it.
                nr_data, errors = await _resolve_nr_stop(
                    self.hass,
                    user_input,
                    stop[CONF_STATION],
                    StationOption(
                        label="",
                        name=stop.get("station_display_name") or "",
                        display_name="",
                    ),
                )

        if user_input is not None and not errors:
            self._stops[self._editing_index] = {
                **stop,
                **nr_data,
                CONF_MAX: user_input[CONF_MAX],
                CONF_PLATFORM: user_input[CONF_PLATFORM],
                CONF_SHORTEN_STATION_NAMES: user_input[CONF_SHORTEN_STATION_NAMES],
            }
            return self._save()

        extra_fields: dict = {}
        if needs_nr_keys:
            for key in (CONF_RDM_API_KEY, CONF_NR_API_KEY, CONF_CRS):
                extra_fields[
                    vol.Optional(key, description={"suggested_value": stop.get(key)})
                ] = cv.string

        return self.async_show_form(
            step_id="edit_station",
            errors=errors,
            description_placeholders={
                **_DESCRIPTION_PLACEHOLDERS,
                "stop_name": self._stop_label(stop),
            },
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

