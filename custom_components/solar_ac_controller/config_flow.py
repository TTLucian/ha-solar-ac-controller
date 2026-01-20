


# --- IMPORTS: All imports at the top for performance and clarity ---
from __future__ import annotations

import voluptuous as vol
from typing import Any
from homeassistant import config_entries
from homeassistant.core import callback, HomeAssistant
from homeassistant.helpers.selector import selector

from .const import (
    DOMAIN,
    CONF_SOLAR_SENSOR,
    CONF_GRID_SENSOR,
    CONF_AC_POWER_SENSOR,
    CONF_AC_SWITCH,
    CONF_ZONES,
    CONF_SOLAR_THRESHOLD_ON,
    CONF_SOLAR_THRESHOLD_OFF,
    CONF_PANIC_THRESHOLD,
    CONF_PANIC_DELAY,
    CONF_MANUAL_LOCK_SECONDS,
    CONF_SHORT_CYCLE_ON_SECONDS,
    CONF_SHORT_CYCLE_OFF_SECONDS,
    CONF_ACTION_DELAY_SECONDS,
    CONF_ADD_CONFIDENCE,
    CONF_REMOVE_CONFIDENCE,
    CONF_INITIAL_LEARNED_POWER,
    CONF_ENABLE_DIAGNOSTICS,
    CONF_OUTSIDE_SENSOR,
    CONF_SEASON_MODE,
    CONF_ENABLE_TEMP_MODULATION,
    CONF_MAX_TEMP_WINTER,
    CONF_MIN_TEMP_SUMMER,
    CONF_ZONE_TEMP_SENSORS,
    CONF_ZONE_MANUAL_POWER,
    DEFAULT_SOLAR_THRESHOLD_ON,
    DEFAULT_SOLAR_THRESHOLD_OFF,
    DEFAULT_PANIC_THRESHOLD,
    DEFAULT_PANIC_DELAY,
    DEFAULT_MANUAL_LOCK_SECONDS,
    DEFAULT_SHORT_CYCLE_ON_SECONDS,
    DEFAULT_SHORT_CYCLE_OFF_SECONDS,
    DEFAULT_ACTION_DELAY_SECONDS,
    DEFAULT_ADD_CONFIDENCE,
    DEFAULT_REMOVE_CONFIDENCE,
    DEFAULT_INITIAL_LEARNED_POWER,
    DEFAULT_SEASON_MODE,
    DEFAULT_ENABLE_TEMP_MODULATION,
    DEFAULT_MAX_TEMP_WINTER,
    DEFAULT_MIN_TEMP_SUMMER,
)



# --- HELPERS: Must be defined before use ---
def _int_field(default: int, minimum: int = 0) -> vol.All:
    """Helper to ensure numeric fields are coerced correctly."""
    def _coerce_int(val):
        try:
            if val is None or (isinstance(val, str) and val.strip() == ""):
                return default
            return int(val)
        except (ValueError, TypeError):
            return default
    return vol.All(_coerce_int, vol.Range(min=minimum))

def parse_numeric_list(val: Any) -> list[float] | None:
    """Helper to convert various inputs into a list of floats."""
    if not val:
        return []
    if isinstance(val, (list, tuple)):
        try:
            return [float(x) for x in val]
        except (ValueError, TypeError):
            return None
    try:
        return [float(x) for x in str(val).replace(",", " ").split()]
    except (ValueError, TypeError):
        return None

# --- SHARED BASE FLOW FOR VALIDATION & CLEANING ---
class SolarACBaseFlow:
    def validate_solar_hysteresis(self, user_input, errors):
        on_val = user_input.get(CONF_SOLAR_THRESHOLD_ON, getattr(self, 'data', {}).get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON))
        off_val = user_input.get(CONF_SOLAR_THRESHOLD_OFF, getattr(self, 'data', {}).get(CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF))
        if int(off_val) >= int(on_val):
            errors["base"] = "invalid_solar_hysteresis"
        return errors

    def validate_panic_threshold(self, user_input, errors):
        panic_th = user_input.get(CONF_PANIC_THRESHOLD, getattr(self, 'data', {}).get(CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD))
        solar_on = getattr(self, 'data', {}).get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON)
        if int(panic_th) <= int(solar_on):
            errors["base"] = "panic_too_low"
        return errors

    def clean_zone_temp_sensors(self, zones, zone_temp_sensors):
        if not isinstance(zone_temp_sensors, list):
            if zone_temp_sensors is None or zone_temp_sensors == "":
                zone_temp_sensors = []
            else:
                zone_temp_sensors = [zone_temp_sensors]
        if len(zone_temp_sensors) < len(zones):
            zone_temp_sensors = list(zone_temp_sensors) + [""] * (len(zones) - len(zone_temp_sensors))
        if len(zone_temp_sensors) > len(zones):
            zone_temp_sensors = zone_temp_sensors[:len(zones)]
        return zone_temp_sensors

    def clean_zone_manual_power(self, zones, zone_manual_power):
        if isinstance(zone_manual_power, (list, tuple)):
            return ", ".join(str(v) for v in zone_manual_power)
        elif zone_manual_power is None:
            return ""
        else:
            return str(zone_manual_power)

def schema_user(defaults):
    return vol.Schema({
        vol.Required(
            CONF_ZONES, default=defaults.get(CONF_ZONES, [])
        ): selector({
            "entity": {"domain": ["climate", "switch", "fan"], "multiple": True}
        }),
        vol.Optional(
            CONF_AC_SWITCH, default=defaults.get(CONF_AC_SWITCH, "")
        ): selector({"entity": {"domain": "switch"}}),
        vol.Required(
            CONF_SOLAR_SENSOR, default=defaults.get(CONF_SOLAR_SENSOR)
        ): selector({"entity": {"domain": "sensor"}}),
        vol.Required(
            CONF_GRID_SENSOR, default=defaults.get(CONF_GRID_SENSOR)
        ): selector({"entity": {"domain": "sensor"}}),
        vol.Required(
            CONF_AC_POWER_SENSOR, default=defaults.get(CONF_AC_POWER_SENSOR)
        ): selector({"entity": {"domain": "sensor"}}),
        vol.Optional(
            CONF_SOLAR_THRESHOLD_ON,
            default=int(defaults.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON)),
        ): _int_field(int(DEFAULT_SOLAR_THRESHOLD_ON), minimum=0),
        vol.Optional(
            CONF_SOLAR_THRESHOLD_OFF,
            default=int(defaults.get(CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF)),
        ): _int_field(int(DEFAULT_SOLAR_THRESHOLD_OFF), minimum=0),
        vol.Optional(
            CONF_ADD_CONFIDENCE,
            default=int(defaults.get(CONF_ADD_CONFIDENCE, DEFAULT_ADD_CONFIDENCE)),
        ): _int_field(int(DEFAULT_ADD_CONFIDENCE), minimum=0),
        vol.Optional(
            CONF_REMOVE_CONFIDENCE,
            default=int(defaults.get(CONF_REMOVE_CONFIDENCE, DEFAULT_REMOVE_CONFIDENCE)),
        ): _int_field(int(DEFAULT_REMOVE_CONFIDENCE), minimum=0),
        vol.Optional(
            CONF_INITIAL_LEARNED_POWER,
            default=int(defaults.get(CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER)),
        ): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional(
            CONF_ENABLE_TEMP_MODULATION,
            default=bool(defaults.get(CONF_ENABLE_TEMP_MODULATION, DEFAULT_ENABLE_TEMP_MODULATION)),
        ): bool,
        vol.Optional(
            CONF_ENABLE_DIAGNOSTICS,
            default=bool(defaults.get(CONF_ENABLE_DIAGNOSTICS, False)),
        ): bool,
    })

def schema_timing(defaults):
    return vol.Schema({
        vol.Optional(
            CONF_ACTION_DELAY_SECONDS,
            default=int(defaults.get(CONF_ACTION_DELAY_SECONDS, DEFAULT_ACTION_DELAY_SECONDS)),
        ): _int_field(int(DEFAULT_ACTION_DELAY_SECONDS), minimum=0),
        vol.Optional(
            CONF_MANUAL_LOCK_SECONDS,
            default=int(defaults.get(CONF_MANUAL_LOCK_SECONDS, DEFAULT_MANUAL_LOCK_SECONDS)),
        ): _int_field(int(DEFAULT_MANUAL_LOCK_SECONDS), minimum=0),
        vol.Optional(
            CONF_SHORT_CYCLE_ON_SECONDS,
            default=int(defaults.get(CONF_SHORT_CYCLE_ON_SECONDS, DEFAULT_SHORT_CYCLE_ON_SECONDS)),
        ): _int_field(int(DEFAULT_SHORT_CYCLE_ON_SECONDS), minimum=0),
        vol.Optional(
            CONF_SHORT_CYCLE_OFF_SECONDS,
            default=int(defaults.get(CONF_SHORT_CYCLE_OFF_SECONDS, DEFAULT_SHORT_CYCLE_OFF_SECONDS)),
        ): _int_field(int(DEFAULT_SHORT_CYCLE_OFF_SECONDS), minimum=0),
        vol.Optional(
            CONF_PANIC_THRESHOLD,
            default=int(defaults.get(CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD)),
        ): _int_field(int(DEFAULT_PANIC_THRESHOLD), minimum=0),
        vol.Optional(
            CONF_PANIC_DELAY,
            default=int(defaults.get(CONF_PANIC_DELAY, DEFAULT_PANIC_DELAY)),
        ): _int_field(int(DEFAULT_PANIC_DELAY), minimum=0),
    })

def schema_comfort(defaults, zone_manual_default):
    return vol.Schema({
        vol.Optional(
            CONF_ZONE_TEMP_SENSORS,
            default=defaults.get(CONF_ZONE_TEMP_SENSORS, []),
        ): selector({
            "entity": {"domain": "sensor", "device_class": ["temperature"], "multiple": True}
        }),
        vol.Optional(
            CONF_ZONE_MANUAL_POWER,
            default=zone_manual_default,
        ): selector({"text": {"multiline": False}}),
        vol.Optional(
            CONF_MAX_TEMP_WINTER,
            default=float(defaults.get(CONF_MAX_TEMP_WINTER, DEFAULT_MAX_TEMP_WINTER)),
        ): vol.Coerce(float),
        vol.Optional(
            CONF_MIN_TEMP_SUMMER,
            default=float(defaults.get(CONF_MIN_TEMP_SUMMER, DEFAULT_MIN_TEMP_SUMMER)),
        ): vol.Coerce(float),
    })




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



class SolarACConfigFlow(SolarACBaseFlow, config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[misc]
    def _is_reconfigure(self) -> bool:
        return getattr(self, "_reconfigure_mode", False)

    def _set_reconfigure(self, value: bool = True):
        self._reconfigure_mode = value




    VERSION = 1

    def __init__(self):
        super().__init__()
        self._reconfigure_defaults = {}
        self.data = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
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
                errors = self.validate_solar_hysteresis(user_input, errors)
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
        defaults = {**self._reconfigure_defaults, **self.data}
        if user_input is not None:
            errors = self.validate_panic_threshold(user_input, errors)
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
        defaults = {**self._reconfigure_defaults, **self.data}
        _parse_manual_power = parse_numeric_list
        if user_input is not None:
            zones = self.data.get(CONF_ZONES, [])
            zone_temp_sensors = user_input.get(CONF_ZONE_TEMP_SENSORS, [])
            zone_manual_power = user_input.get(CONF_ZONE_MANUAL_POWER) or ""
            if zone_temp_sensors and len(zone_temp_sensors) != len(zones):
                errors[CONF_ZONE_TEMP_SENSORS] = "zone_temp_sensors_mismatch"
            if not errors:
                zone_temp_sensors = self.clean_zone_temp_sensors(zones, zone_temp_sensors)
            validation_error = await _validate_zone_temp_sensors(
                self.hass, zones, zone_temp_sensors
            )
            if validation_error:
                errors["base"] = validation_error
            parsed_power = _parse_manual_power(zone_manual_power)
            if parsed_power is None:
                errors[CONF_ZONE_MANUAL_POWER] = "invalid_manual_power"
            elif parsed_power and len(parsed_power) != len(zones):
                errors[CONF_ZONE_MANUAL_POWER] = "manual_power_count_mismatch"
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
        zone_manual_default = self.clean_zone_manual_power(
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
            self._set_reconfigure(True)
            self._reconfigure_entry = entry
            if user_input is not None:
                await self.hass.config_entries.async_update_entry(
                    entry,
                    options={**entry.options, **user_input},
                )
                return self.async_abort(reason="reconfigured")
        else:
            self._reconfigure_defaults = {}
            self._set_reconfigure(False)

        return await self.async_step_user(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return SolarACOptionsFlowHandler(config_entry)


class SolarACOptionsFlowHandler(SolarACBaseFlow, config_entries.OptionsFlow):
    """Handle runtime configuration changes."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
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
                errors = self.validate_solar_hysteresis(user_input, errors)
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
            errors = self.validate_panic_threshold(user_input, errors)
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
                zone_temp_sensors = self.clean_zone_temp_sensors(zones, zone_temp_sensors)
            validation_error = await _validate_zone_temp_sensors(
                self.hass, zones, zone_temp_sensors
            )
            if validation_error:
                errors["base"] = validation_error
            parsed_power = _parse_manual_power(zone_manual_power)
            if parsed_power is None:
                errors[CONF_ZONE_MANUAL_POWER] = "invalid_manual_power"
            elif parsed_power and len(parsed_power) != len(zones):
                errors[CONF_ZONE_MANUAL_POWER] = "manual_power_count_mismatch"
            if not errors:
                cleaned_input = dict(user_input)
                cleaned_input[CONF_ZONE_TEMP_SENSORS] = zone_temp_sensors
                if parsed_power is not None:
                    cleaned_input[CONF_ZONE_MANUAL_POWER] = parsed_power
                self.data = {**self.data, **cleaned_input}
                return self.async_create_entry(title="", data=self.data)
        # Always show manual power as a string for UI
        zone_manual_default = self.clean_zone_manual_power(
            self.data.get(CONF_ZONES, []), defaults.get(CONF_ZONE_MANUAL_POWER, "")
        )
        schema = schema_comfort(defaults, zone_manual_default)
        return self.async_show_form(
            step_id="comfort",
            data_schema=schema,
            errors=errors,
        )
