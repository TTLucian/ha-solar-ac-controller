from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    CONF_AC_POWER_SENSOR,
    CONF_AC_SWITCH,
    CONF_ACTION_DELAY_SECONDS,
    CONF_ADD_CONFIDENCE,
    CONF_ENABLE_DIAGNOSTICS_SENSOR,
    CONF_ENABLE_TEMP_MODULATION,
    CONF_GRID_SENSOR,
    CONF_INITIAL_LEARNED_POWER,
    CONF_MANUAL_LOCK_SECONDS,
    CONF_MAX_TEMP_WINTER,
    CONF_MIN_TEMP_SUMMER,
    CONF_PANIC_DELAY,
    CONF_PANIC_THRESHOLD,
    CONF_REMOVE_CONFIDENCE,
    CONF_SHORT_CYCLE_OFF_SECONDS,
    CONF_SHORT_CYCLE_ON_SECONDS,
    CONF_SOLAR_SENSOR,
    CONF_SOLAR_THRESHOLD_OFF,
    CONF_SOLAR_THRESHOLD_ON,
    CONF_ZONE_MANUAL_POWER,
    CONF_ZONE_TEMP_SENSORS,
    CONF_ZONES,
    DEFAULT_ACTION_DELAY_SECONDS,
    DEFAULT_ADD_CONFIDENCE,
    DEFAULT_ENABLE_TEMP_MODULATION,
    DEFAULT_INITIAL_LEARNED_POWER,
    DEFAULT_MANUAL_LOCK_SECONDS,
    DEFAULT_MAX_TEMP_WINTER,
    DEFAULT_MIN_TEMP_SUMMER,
    DEFAULT_PANIC_DELAY,
    DEFAULT_PANIC_THRESHOLD,
    DEFAULT_REMOVE_CONFIDENCE,
    DEFAULT_SHORT_CYCLE_OFF_SECONDS,
    DEFAULT_SHORT_CYCLE_ON_SECONDS,
    DEFAULT_SOLAR_THRESHOLD_OFF,
    DEFAULT_SOLAR_THRESHOLD_ON,
    DOMAIN,
)

# --- HELPERS: Must be defined before use ---


def parse_numeric_list(val: Any) -> list[float | None] | None:
    """Helper to convert various inputs into a list of floats/None.

    Empty strings in comma-separated input become None (use learned power).
    """
    if not val:
        return []
    if isinstance(val, (list, tuple)):
        try:
            return [float(x) if x != "" else None for x in val]
        except (ValueError, TypeError):
            return None
    try:
        # Split and filter out empty strings, but preserve position with None
        parts = [x.strip() for x in str(val).replace(",", " ").split()]
        result = []
        for part in parts:
            if part == "":
                result.append(None)
            else:
                try:
                    result.append(float(part))
                except (ValueError, TypeError):
                    return None
        return result
    except (ValueError, TypeError):
        return None


# --- SHARED BASE FLOW FOR VALIDATION & CLEANING ---
def validate_solar_hysteresis(user_input, data, errors):
    on_val = user_input.get(
        CONF_SOLAR_THRESHOLD_ON,
        data.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON),
    )
    off_val = user_input.get(
        CONF_SOLAR_THRESHOLD_OFF,
        data.get(CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF),
    )
    if int(off_val) >= int(on_val):
        errors["base"] = "invalid_solar_hysteresis"
    return errors


def validate_panic_threshold(user_input, data, errors):
    panic_th = user_input.get(
        CONF_PANIC_THRESHOLD, data.get(CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD)
    )
    solar_on = data.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON)
    if int(panic_th) <= int(solar_on):
        errors["base"] = "panic_too_low"
    return errors


def clean_zone_temp_sensors(zones, zone_temp_sensors):
    if not isinstance(zone_temp_sensors, list):
        if zone_temp_sensors is None or zone_temp_sensors == "":
            zone_temp_sensors = []
        else:
            zone_temp_sensors = [zone_temp_sensors]
    if len(zone_temp_sensors) < len(zones):
        zone_temp_sensors = list(zone_temp_sensors) + [""] * (
            len(zones) - len(zone_temp_sensors)
        )
    if len(zone_temp_sensors) > len(zones):
        zone_temp_sensors = zone_temp_sensors[: len(zones)]
    return zone_temp_sensors


def clean_zone_manual_power(zones, zone_manual_power):
    if isinstance(zone_manual_power, (list, tuple)):
        # Convert None values to empty strings for display
        return ", ".join("" if v is None else str(v) for v in zone_manual_power)
    elif zone_manual_power is None:
        return ""
    else:
        return str(zone_manual_power)


def schema_user(defaults):
    return vol.Schema(
        {
            vol.Required(
                CONF_ZONES, default=defaults.get(CONF_ZONES, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["climate", "switch", "fan"], multiple=True
                )
            ),
            vol.Optional(
                CONF_AC_SWITCH, default=defaults.get(CONF_AC_SWITCH, "")
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            vol.Required(
                CONF_SOLAR_SENSOR, default=defaults.get(CONF_SOLAR_SENSOR)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_GRID_SENSOR, default=defaults.get(CONF_GRID_SENSOR)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_AC_POWER_SENSOR, default=defaults.get(CONF_AC_POWER_SENSOR)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(
                CONF_SOLAR_THRESHOLD_ON,
                default=int(
                    defaults.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON)
                ),
            ): int,
            vol.Optional(
                CONF_SOLAR_THRESHOLD_OFF,
                default=int(
                    defaults.get(CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF)
                ),
            ): int,
            vol.Optional(
                CONF_ADD_CONFIDENCE,
                default=int(defaults.get(CONF_ADD_CONFIDENCE, DEFAULT_ADD_CONFIDENCE)),
            ): int,
            vol.Optional(
                CONF_REMOVE_CONFIDENCE,
                default=int(
                    defaults.get(CONF_REMOVE_CONFIDENCE, DEFAULT_REMOVE_CONFIDENCE)
                ),
            ): int,
            vol.Optional(
                CONF_INITIAL_LEARNED_POWER,
                default=int(
                    defaults.get(
                        CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER
                    )
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=0)),
            vol.Optional(
                CONF_ENABLE_TEMP_MODULATION,
                default=bool(
                    defaults.get(
                        CONF_ENABLE_TEMP_MODULATION, DEFAULT_ENABLE_TEMP_MODULATION
                    )
                ),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_ENABLE_DIAGNOSTICS_SENSOR,
                default=bool(defaults.get(CONF_ENABLE_DIAGNOSTICS_SENSOR, False)),
            ): selector.BooleanSelector(),
        }
    )


def schema_timing(defaults):
    return vol.Schema(
        {
            vol.Optional(
                CONF_ACTION_DELAY_SECONDS,
                default=int(
                    defaults.get(
                        CONF_ACTION_DELAY_SECONDS, DEFAULT_ACTION_DELAY_SECONDS
                    )
                ),
            ): int,
            vol.Optional(
                CONF_MANUAL_LOCK_SECONDS,
                default=int(
                    defaults.get(CONF_MANUAL_LOCK_SECONDS, DEFAULT_MANUAL_LOCK_SECONDS)
                ),
            ): int,
            vol.Optional(
                CONF_SHORT_CYCLE_ON_SECONDS,
                default=int(
                    defaults.get(
                        CONF_SHORT_CYCLE_ON_SECONDS, DEFAULT_SHORT_CYCLE_ON_SECONDS
                    )
                ),
            ): int,
            vol.Optional(
                CONF_SHORT_CYCLE_OFF_SECONDS,
                default=int(
                    defaults.get(
                        CONF_SHORT_CYCLE_OFF_SECONDS, DEFAULT_SHORT_CYCLE_OFF_SECONDS
                    )
                ),
            ): int,
            vol.Optional(
                CONF_PANIC_THRESHOLD,
                default=int(
                    defaults.get(CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD)
                ),
            ): int,
            vol.Optional(
                CONF_PANIC_DELAY,
                default=int(defaults.get(CONF_PANIC_DELAY, DEFAULT_PANIC_DELAY)),
            ): int,
        }
    )


def schema_comfort(defaults, zone_manual_default):
    return vol.Schema(
        {
            vol.Optional(
                CONF_ZONE_TEMP_SENSORS,
                default=defaults.get(CONF_ZONE_TEMP_SENSORS, []),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor", device_class="temperature", multiple=True
                )
            ),
            vol.Optional(
                CONF_ZONE_MANUAL_POWER,
                default=zone_manual_default,
            ): selector.TextSelector(selector.TextSelectorConfig(multiline=False)),
            vol.Optional(
                CONF_MAX_TEMP_WINTER,
                default=defaults.get(CONF_MAX_TEMP_WINTER, DEFAULT_MAX_TEMP_WINTER),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_MIN_TEMP_SUMMER,
                default=defaults.get(CONF_MIN_TEMP_SUMMER, DEFAULT_MIN_TEMP_SUMMER),
            ): vol.Coerce(float),
        }
    )


async def _validate_zone_temp_sensors(
    hass: HomeAssistant, zones: list[str], sensors: list[str]
) -> str | None:
    """
    Validate zone-sensor mapping when temperature modulation is enabled.
    - Non-climate zones MUST have external temperature sensors
    - Climate zones MAY have external sensors (optional override)

    Returns error key if validation fails, None if OK.
    """
    if not zones:
        return None

    non_climate_missing_sensors = []
    missing_entities = []

    for idx, zone_id in enumerate(zones):
        state_obj = hass.states.get(zone_id)
        if not state_obj:
            # Entity doesn't exist or is unavailable
            missing_entities.append(zone_id)
            continue

        entity_domain = state_obj.domain
        has_sensor = idx < len(sensors) and sensors[idx]

        # Non-climate entities require external sensor
        if entity_domain not in ("climate",) and not has_sensor:
            zone_name = zone_id.split(".")[-1] if zone_id else f"Zone {idx + 1}"
            non_climate_missing_sensors.append(f"{zone_name} ({entity_domain})")

    if missing_entities:
        return "zone_entity_missing"
    if non_climate_missing_sensors:
        return "missing_temp_sensors_non_climate"
    return None


class SolarACConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if not hasattr(self, "_reconfigure_defaults"):
            self._reconfigure_defaults = {}
        if not hasattr(self, "data"):
            self.data = {}
        defaults = {**self._reconfigure_defaults, **self.data}
        if user_input is not None:
            zones = user_input.get(CONF_ZONES, [])
            solar_sensor = user_input.get(CONF_SOLAR_SENSOR)
            grid_sensor = user_input.get(CONF_GRID_SENSOR)
            ac_power_sensor = user_input.get(CONF_AC_POWER_SENSOR)
            # Unique ID logic: prevent duplicate integration for same sensors
            unique_id = f"{solar_sensor}|{grid_sensor}|{ac_power_sensor}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            if not zones:
                errors["base"] = "no_zones"
            else:
                errors = validate_solar_hysteresis(user_input, self.data, errors)
            if not errors:
                self.data = {**self.data, **user_input}
                return await self.async_step_timing()
        schema = schema_user(defaults)
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_timing(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if not hasattr(self, "_reconfigure_defaults"):
            self._reconfigure_defaults = {}
        if not hasattr(self, "data"):
            self.data = {}
        defaults = {**self._reconfigure_defaults, **self.data}
        if user_input is not None:
            errors = validate_panic_threshold(user_input, self.data, errors)
            if not errors:
                self.data = {**self.data, **user_input}
                if self.data.get(CONF_ENABLE_TEMP_MODULATION):
                    return await self.async_step_comfort()
                else:
                    return self.async_create_entry(
                        title="Solar AC Controller", data=self.data
                    )
        schema = schema_timing(defaults)
        return self.async_show_form(
            step_id="timing",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_comfort(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if not hasattr(self, "_reconfigure_defaults"):
            self._reconfigure_defaults = {}
        if not hasattr(self, "data"):
            self.data = {}
        defaults = {**self._reconfigure_defaults, **self.data}
        _parse_manual_power = parse_numeric_list
        if user_input is not None:
            zones = self.data.get(CONF_ZONES, [])
            zone_temp_sensors = user_input.get(CONF_ZONE_TEMP_SENSORS, [])
            zone_manual_power = user_input.get(CONF_ZONE_MANUAL_POWER) or ""
            if zone_temp_sensors and len(zone_temp_sensors) != len(zones):
                errors[CONF_ZONE_TEMP_SENSORS] = "zone_temp_sensors_mismatch"
            if not errors:
                zone_temp_sensors = clean_zone_temp_sensors(zones, zone_temp_sensors)
            validation_error = await _validate_zone_temp_sensors(
                self.hass, zones, zone_temp_sensors
            )
            if validation_error:
                errors["base"] = validation_error
            parsed_power = _parse_manual_power(zone_manual_power)
            if parsed_power is None:
                errors[CONF_ZONE_MANUAL_POWER] = "invalid_manual_power"
            elif parsed_power and len(parsed_power) > len(zones):
                errors[CONF_ZONE_MANUAL_POWER] = "manual_power_count_mismatch"
            elif parsed_power:
                # Pad with None for missing zones
                while len(parsed_power) < len(zones):
                    parsed_power.append(None)
            if not errors:
                cleaned_input = dict(user_input)
                cleaned_input[CONF_ZONE_TEMP_SENSORS] = zone_temp_sensors
                if parsed_power is not None:
                    cleaned_input[CONF_ZONE_MANUAL_POWER] = parsed_power
                self.data = {**self.data, **cleaned_input}
                return self.async_create_entry(
                    title="Solar AC Controller", data=self.data
                )
        # Always show manual power as a string for UI
        zone_manual_default = clean_zone_manual_power(
            self.data.get(CONF_ZONES, []), defaults.get(CONF_ZONE_MANUAL_POWER, "")
        )
        schema = schema_comfort(defaults, zone_manual_default)
        return self.async_show_form(
            step_id="comfort",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_import(self, user_input: dict[str, Any]):
        return await self.async_step_user(user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        """Handle reconfigure flow by seeding defaults from existing entry and updating, not duplicating."""
        entry_id = self.context.get("entry_id")
        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        if entry:
            self._reconfigure_defaults = {**entry.data, **entry.options}
            self.context["title_placeholders"] = {"name": entry.title}
            if user_input is not None:
                self.hass.config_entries.async_update_entry(
                    entry,
                    options={**entry.options, **user_input},
                )
                return self.async_abort(reason="reconfigured")
        else:
            self._reconfigure_defaults = {}

        return await self.async_step_user(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        return SolarACOptionsFlowHandler(config_entry)


class SolarACOptionsFlowHandler(OptionsFlow):
    """Handle runtime configuration changes."""

    def __init__(self, config_entry: ConfigEntry):
        self.entry = config_entry
        # Always merge data and options for a complete working set
        self.data = {**config_entry.data, **config_entry.options}

    @property
    def _current(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options, **self.data}

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        defaults = self._current
        if user_input is not None:
            zones = user_input.get(CONF_ZONES, [])
            if not zones:
                errors["base"] = "no_zones"
            else:
                errors = validate_solar_hysteresis(user_input, self.data, errors)
            if not errors:
                self.data = {**self.data, **user_input}
                return await self.async_step_timing()
        schema = schema_user(defaults)
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_timing(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        defaults = {**self._current, **self.data}
        if user_input is not None:
            errors = validate_panic_threshold(user_input, self.data, errors)
            if not errors:
                self.data = {**self.data, **user_input}
                if self.data.get(CONF_ENABLE_TEMP_MODULATION):
                    return await self.async_step_comfort()
                else:
                    return self.async_create_entry(title="", data=self.data)
        schema = schema_timing(defaults)
        return self.async_show_form(
            step_id="timing",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_comfort(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        defaults = {**self._current, **self.data}
        _parse_manual_power = parse_numeric_list
        if user_input is not None:
            zones = self.data.get(CONF_ZONES, [])
            zone_temp_sensors = user_input.get(CONF_ZONE_TEMP_SENSORS, [])
            zone_manual_power = user_input.get(CONF_ZONE_MANUAL_POWER) or ""
            if zone_temp_sensors and len(zone_temp_sensors) != len(zones):
                errors[CONF_ZONE_TEMP_SENSORS] = "zone_temp_sensors_mismatch"
            if not errors:
                zone_temp_sensors = clean_zone_temp_sensors(zones, zone_temp_sensors)
            validation_error = await _validate_zone_temp_sensors(
                self.hass, zones, zone_temp_sensors
            )
            if validation_error:
                errors["base"] = validation_error
            parsed_power = _parse_manual_power(zone_manual_power)
            if parsed_power is None:
                errors[CONF_ZONE_MANUAL_POWER] = "invalid_manual_power"
            elif parsed_power and len(parsed_power) > len(zones):
                errors[CONF_ZONE_MANUAL_POWER] = "manual_power_count_mismatch"
            elif parsed_power:
                # Pad with None for missing zones
                while len(parsed_power) < len(zones):
                    parsed_power.append(None)
            if not errors:
                cleaned_input = dict(user_input)
                cleaned_input[CONF_ZONE_TEMP_SENSORS] = zone_temp_sensors
                if parsed_power is not None:
                    cleaned_input[CONF_ZONE_MANUAL_POWER] = parsed_power
                self.data = {**self.data, **cleaned_input}
                return self.async_create_entry(title="", data=self.data)
        # Always show manual power as a string for UI
        zone_manual_default = clean_zone_manual_power(
            self.data.get(CONF_ZONES, []), defaults.get(CONF_ZONE_MANUAL_POWER, "")
        )
        schema = schema_comfort(defaults, zone_manual_default)
        return self.async_show_form(
            step_id="comfort",
            data_schema=schema,
            errors=errors,
        )
