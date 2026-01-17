from __future__ import annotations

import voluptuous as vol
from typing import Any
from homeassistant import config_entries
from homeassistant.core import callback
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
    CONF_ENABLE_AUTO_SEASON,
    CONF_ENABLE_TEMP_MODULATION,
    CONF_MASTER_SWITCH_IN_OFFSEASON,
    CONF_HEAT_ON_BELOW,
    CONF_HEAT_OFF_ABOVE,
    CONF_COOL_ON_ABOVE,
    CONF_COOL_OFF_BELOW,
    CONF_VERY_COLD_THRESHOLD,
    CONF_CHILLY_THRESHOLD,
    CONF_COMFORTABLE_THRESHOLD,
    CONF_MAX_TEMP_WINTER,
    CONF_MIN_TEMP_SUMMER,
    CONF_ZONE_TEMP_SENSORS,
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
    DEFAULT_ENABLE_AUTO_SEASON,
    DEFAULT_ENABLE_TEMP_MODULATION,
    DEFAULT_MASTER_SWITCH_IN_OFFSEASON,
    DEFAULT_HEAT_ON_BELOW,
    DEFAULT_HEAT_OFF_ABOVE,
    DEFAULT_COOL_ON_ABOVE,
    DEFAULT_COOL_OFF_BELOW,
    DEFAULT_VERY_COLD_THRESHOLD,
    DEFAULT_CHILLY_THRESHOLD,
    DEFAULT_COMFORTABLE_THRESHOLD,
    DEFAULT_MAX_TEMP_WINTER,
    DEFAULT_MIN_TEMP_SUMMER,
)


def _int_field(default: int, minimum: int = 0) -> vol.All:
    # vol.Default does not exist; set default in the schema, not here
    return vol.All(vol.Coerce(int), vol.Range(min=minimum))


class SolarACConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of the Solar AC Controller."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        # When invoked via reconfigure, use existing entry data/options as defaults.
        self._reconfigure_defaults: dict[str, Any] = {}
        # Store form data across steps
        self.data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Step 1: Core Setup - Zones, sensors, solar thresholds, feature toggles."""
        errors: dict[str, str] = {}
        defaults = {**self._reconfigure_defaults, **self.data}

        if user_input is not None:
            zones = user_input.get(CONF_ZONES, [])
            
            if not zones:
                errors["base"] = "no_zones"
            else:
                # Hysteresis validation
                solar_on = int(user_input.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON))
                solar_off = int(user_input.get(CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF))
                
                if solar_off >= solar_on:
                    errors["base"] = "invalid_solar_hysteresis"
            
            if not errors:
                # Store data for next steps
                self.data = {**self.data, **user_input}
                # Proceed to timing step
                return await self.async_step_timing()

        schema = vol.Schema(
            {
                # GROUP 1: What to Control
                vol.Required(CONF_ZONES, default=defaults.get(CONF_ZONES, [])): selector({
                    "entity": {"domain": ["climate", "switch", "fan"], "multiple": True}
                }),
                vol.Optional(CONF_AC_SWITCH, default=defaults.get(CONF_AC_SWITCH, "")): selector({"entity": {"domain": "switch"}}),
                
                # GROUP 2: Sensors
                vol.Required(CONF_SOLAR_SENSOR, default=defaults.get(CONF_SOLAR_SENSOR)): selector({"entity": {"domain": "sensor"}}),
                vol.Required(CONF_GRID_SENSOR, default=defaults.get(CONF_GRID_SENSOR)): selector({"entity": {"domain": "sensor"}}),
                vol.Required(CONF_AC_POWER_SENSOR, default=defaults.get(CONF_AC_POWER_SENSOR)): selector({"entity": {"domain": "sensor"}}),
                
                # GROUP 3: When to Activate
                vol.Optional(CONF_SOLAR_THRESHOLD_ON, default=int(defaults.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON))): _int_field(int(DEFAULT_SOLAR_THRESHOLD_ON), minimum=0),
                vol.Optional(CONF_SOLAR_THRESHOLD_OFF, default=int(defaults.get(CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF))): _int_field(int(DEFAULT_SOLAR_THRESHOLD_OFF), minimum=0),
                
                # GROUP 4: How Aggressive to Add/Remove
                vol.Optional(CONF_ADD_CONFIDENCE, default=int(defaults.get(CONF_ADD_CONFIDENCE, DEFAULT_ADD_CONFIDENCE))): _int_field(int(DEFAULT_ADD_CONFIDENCE), minimum=0),
                vol.Optional(CONF_REMOVE_CONFIDENCE, default=int(defaults.get(CONF_REMOVE_CONFIDENCE, DEFAULT_REMOVE_CONFIDENCE))): _int_field(int(DEFAULT_REMOVE_CONFIDENCE), minimum=0),
                vol.Optional(CONF_INITIAL_LEARNED_POWER, default=int(defaults.get(CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER))): vol.All(vol.Coerce(int), vol.Range(min=0)),
                
                # GROUP 5: Optional Features
                vol.Optional(CONF_ENABLE_TEMP_MODULATION, default=bool(defaults.get(CONF_ENABLE_TEMP_MODULATION, DEFAULT_ENABLE_TEMP_MODULATION))): bool,
                vol.Optional(CONF_ENABLE_AUTO_SEASON, default=bool(defaults.get(CONF_ENABLE_AUTO_SEASON, DEFAULT_ENABLE_AUTO_SEASON))): bool,
                vol.Optional(CONF_ENABLE_DIAGNOSTICS, default=bool(defaults.get(CONF_ENABLE_DIAGNOSTICS, False))): bool,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_timing(self, user_input: dict[str, Any] | None = None):
        """Step 2: Timing & Protection - Delays and guards."""
        errors: dict[str, str] = {}
        defaults = {**self._reconfigure_defaults, **self.data}

        if user_input is not None:
            panic_th = int(user_input.get(CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD))
            solar_on = int(self.data.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON))
            
            if panic_th <= solar_on:
                errors["base"] = "panic_too_low"
            
            if not errors:
                self.data = {**self.data, **user_input}
                
                # Decide next step based on feature toggles
                if self.data.get(CONF_ENABLE_TEMP_MODULATION):
                    return await self.async_step_comfort()
                elif self.data.get(CONF_ENABLE_AUTO_SEASON):
                    return await self.async_step_auto_season()
                else:
                    # Create entry if no conditional steps
                    return self.async_create_entry(title="Solar AC Controller", data=self.data)

        schema = vol.Schema(
            {
                vol.Optional(CONF_ACTION_DELAY_SECONDS, default=int(defaults.get(CONF_ACTION_DELAY_SECONDS, DEFAULT_ACTION_DELAY_SECONDS))): _int_field(int(DEFAULT_ACTION_DELAY_SECONDS), minimum=0),
                vol.Optional(CONF_MANUAL_LOCK_SECONDS, default=int(defaults.get(CONF_MANUAL_LOCK_SECONDS, DEFAULT_MANUAL_LOCK_SECONDS))): _int_field(int(DEFAULT_MANUAL_LOCK_SECONDS), minimum=0),
                vol.Optional(CONF_SHORT_CYCLE_ON_SECONDS, default=int(defaults.get(CONF_SHORT_CYCLE_ON_SECONDS, DEFAULT_SHORT_CYCLE_ON_SECONDS))): _int_field(int(DEFAULT_SHORT_CYCLE_ON_SECONDS), minimum=0),
                vol.Optional(CONF_SHORT_CYCLE_OFF_SECONDS, default=int(defaults.get(CONF_SHORT_CYCLE_OFF_SECONDS, DEFAULT_SHORT_CYCLE_OFF_SECONDS))): _int_field(int(DEFAULT_SHORT_CYCLE_OFF_SECONDS), minimum=0),
                vol.Optional(CONF_PANIC_THRESHOLD, default=int(defaults.get(CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD))): _int_field(int(DEFAULT_PANIC_THRESHOLD), minimum=0),
                vol.Optional(CONF_PANIC_DELAY, default=int(defaults.get(CONF_PANIC_DELAY, DEFAULT_PANIC_DELAY))): _int_field(int(DEFAULT_PANIC_DELAY), minimum=0),
            }
        )

        return self.async_show_form(
            step_id="timing",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_comfort(self, user_input: dict[str, Any] | None = None):
        """Step 3: Comfort-Based Zone Control (conditional on ENABLE_TEMP_MODULATION)."""
        errors: dict[str, str] = {}
        defaults = {**self._reconfigure_defaults, **self.data}

        if user_input is not None:
            zones = self.data.get(CONF_ZONES, [])
            zone_temp_sensors = user_input.get(CONF_ZONE_TEMP_SENSORS, [])
            
            # Validate zone_temp_sensors list length matches zones if provided
            if zone_temp_sensors and len(zone_temp_sensors) != len(zones):
                errors["base"] = "zone_temp_sensors_mismatch"
            
            if not errors:
                self.data = {**self.data, **user_input}
                
                # Next step: auto-season if enabled, else create entry
                if self.data.get(CONF_ENABLE_AUTO_SEASON):
                    return await self.async_step_auto_season()
                else:
                    return self.async_create_entry(title="Solar AC Controller", data=self.data)

        schema = vol.Schema(
            {
                vol.Optional(CONF_ZONE_TEMP_SENSORS, default=defaults.get(CONF_ZONE_TEMP_SENSORS, [])): selector({"entity": {"domain": "sensor", "device_class": ["temperature"], "multiple": True}}),
                vol.Optional(CONF_MAX_TEMP_WINTER, default=float(defaults.get(CONF_MAX_TEMP_WINTER, DEFAULT_MAX_TEMP_WINTER))): vol.Coerce(float),
                vol.Optional(CONF_MIN_TEMP_SUMMER, default=float(defaults.get(CONF_MIN_TEMP_SUMMER, DEFAULT_MIN_TEMP_SUMMER))): vol.Coerce(float),
            }
        )

        return self.async_show_form(
            step_id="comfort",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_auto_season(self, user_input: dict[str, Any] | None = None):
        """Step 4: Auto-Season Detection (conditional on ENABLE_AUTO_SEASON)."""
        errors: dict[str, str] = {}
        defaults = {**self._reconfigure_defaults, **self.data}

        if user_input is not None:
            heat_on = float(user_input.get(CONF_HEAT_ON_BELOW, DEFAULT_HEAT_ON_BELOW))
            heat_off = float(user_input.get(CONF_HEAT_OFF_ABOVE, DEFAULT_HEAT_OFF_ABOVE))
            cool_on = float(user_input.get(CONF_COOL_ON_ABOVE, DEFAULT_COOL_ON_ABOVE))
            cool_off = float(user_input.get(CONF_COOL_OFF_BELOW, DEFAULT_COOL_OFF_BELOW))
            very_cold = float(user_input.get(CONF_VERY_COLD_THRESHOLD, DEFAULT_VERY_COLD_THRESHOLD))
            chilly = float(user_input.get(CONF_CHILLY_THRESHOLD, DEFAULT_CHILLY_THRESHOLD))
            comfortable = float(user_input.get(CONF_COMFORTABLE_THRESHOLD, DEFAULT_COMFORTABLE_THRESHOLD))
            
            if heat_off <= heat_on:
                errors["base"] = "invalid_heat_hysteresis"
            elif cool_on <= cool_off:
                errors["base"] = "invalid_cool_hysteresis"
            elif not (very_cold < chilly < comfortable):
                errors["base"] = "invalid_bands"
            
            if not errors:
                self.data = {**self.data, **user_input}
                return self.async_create_entry(title="Solar AC Controller", data=self.data)

        schema = vol.Schema(
            {
                vol.Optional(CONF_OUTSIDE_SENSOR, default=defaults.get(CONF_OUTSIDE_SENSOR) or None): selector({"entity": {"domain": "sensor"}}),
                vol.Optional(CONF_HEAT_ON_BELOW, default=float(defaults.get(CONF_HEAT_ON_BELOW, DEFAULT_HEAT_ON_BELOW))): vol.Coerce(float),
                vol.Optional(CONF_HEAT_OFF_ABOVE, default=float(defaults.get(CONF_HEAT_OFF_ABOVE, DEFAULT_HEAT_OFF_ABOVE))): vol.Coerce(float),
                vol.Optional(CONF_COOL_ON_ABOVE, default=float(defaults.get(CONF_COOL_ON_ABOVE, DEFAULT_COOL_ON_ABOVE))): vol.Coerce(float),
                vol.Optional(CONF_COOL_OFF_BELOW, default=float(defaults.get(CONF_COOL_OFF_BELOW, DEFAULT_COOL_OFF_BELOW))): vol.Coerce(float),
                vol.Optional(CONF_VERY_COLD_THRESHOLD, default=float(defaults.get(CONF_VERY_COLD_THRESHOLD, DEFAULT_VERY_COLD_THRESHOLD))): vol.Coerce(float),
                vol.Optional(CONF_CHILLY_THRESHOLD, default=float(defaults.get(CONF_CHILLY_THRESHOLD, DEFAULT_CHILLY_THRESHOLD))): vol.Coerce(float),
                vol.Optional(CONF_COMFORTABLE_THRESHOLD, default=float(defaults.get(CONF_COMFORTABLE_THRESHOLD, DEFAULT_COMFORTABLE_THRESHOLD))): vol.Coerce(float),
                vol.Optional(CONF_MASTER_SWITCH_IN_OFFSEASON, default=bool(defaults.get(CONF_MASTER_SWITCH_IN_OFFSEASON, DEFAULT_MASTER_SWITCH_IN_OFFSEASON))): bool,
            }
        )

        return self.async_show_form(
            step_id="auto_season",
            data_schema=schema,
            errors=errors,
        )
    async def async_step_import(self, user_input: dict[str, Any]):
        return await self.async_step_user(user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        """Handle reconfigure flow by seeding defaults from existing entry."""
        entry_id = self.context.get("entry_id")
        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        if entry:
            self._reconfigure_defaults = {**entry.data, **entry.options}
            self.context["title_placeholders"] = {"name": entry.title}
        else:
            self._reconfigure_defaults = {}

        return await self.async_step_user(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return SolarACOptionsFlowHandler(config_entry)


class SolarACOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle runtime configuration changes."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        self.entry = config_entry
        self.data: dict[str, Any] = {}

    @property
    def _current(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """First step of options (same as initial user setup)."""
        errors: dict[str, str] = {}
        defaults = self._current

        if user_input is not None:
            zones = user_input.get(CONF_ZONES, [])
            
            if not zones:
                errors["base"] = "no_zones"
            else:
                # Hysteresis validation
                solar_on = int(user_input.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON))
                solar_off = int(user_input.get(CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF))
                
                if solar_off >= solar_on:
                    errors["base"] = "invalid_solar_hysteresis"
            
            if not errors:
                self.data = {**self.data, **user_input}
                return await self.async_step_timing()

        schema = vol.Schema(
            {
                vol.Required(CONF_ZONES, default=defaults.get(CONF_ZONES, [])): selector({
                    "entity": {"domain": ["climate", "switch", "fan"], "multiple": True}
                }),
                vol.Required(CONF_SOLAR_SENSOR, default=defaults.get(CONF_SOLAR_SENSOR)): selector({"entity": {"domain": "sensor"}}),
                vol.Required(CONF_GRID_SENSOR, default=defaults.get(CONF_GRID_SENSOR)): selector({"entity": {"domain": "sensor"}}),
                vol.Required(CONF_AC_POWER_SENSOR, default=defaults.get(CONF_AC_POWER_SENSOR)): selector({"entity": {"domain": "sensor"}}),
                vol.Optional(CONF_AC_SWITCH, default=defaults.get(CONF_AC_SWITCH, "")): selector({"entity": {"domain": "switch"}}),
                
                vol.Optional(CONF_SOLAR_THRESHOLD_ON, default=int(defaults.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON))): _int_field(int(DEFAULT_SOLAR_THRESHOLD_ON), minimum=0),
                vol.Optional(CONF_SOLAR_THRESHOLD_OFF, default=int(defaults.get(CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF))): _int_field(int(DEFAULT_SOLAR_THRESHOLD_OFF), minimum=0),
                
                vol.Optional(CONF_ADD_CONFIDENCE, default=int(defaults.get(CONF_ADD_CONFIDENCE, DEFAULT_ADD_CONFIDENCE))): _int_field(int(DEFAULT_ADD_CONFIDENCE), minimum=0),
                vol.Optional(CONF_REMOVE_CONFIDENCE, default=int(defaults.get(CONF_REMOVE_CONFIDENCE, DEFAULT_REMOVE_CONFIDENCE))): _int_field(int(DEFAULT_REMOVE_CONFIDENCE), minimum=0),
                
                vol.Optional(CONF_INITIAL_LEARNED_POWER, default=int(defaults.get(CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER))): vol.All(vol.Coerce(int), vol.Range(min=0)),
                
                vol.Optional(CONF_ENABLE_TEMP_MODULATION, default=bool(defaults.get(CONF_ENABLE_TEMP_MODULATION, DEFAULT_ENABLE_TEMP_MODULATION))): bool,
                vol.Optional(CONF_ENABLE_AUTO_SEASON, default=bool(defaults.get(CONF_ENABLE_AUTO_SEASON, DEFAULT_ENABLE_AUTO_SEASON))): bool,
                vol.Optional(CONF_ENABLE_DIAGNOSTICS, default=bool(defaults.get(CONF_ENABLE_DIAGNOSTICS, False))): bool,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_timing(self, user_input: dict[str, Any] | None = None):
        """Step 2: Timing & Protection."""
        errors: dict[str, str] = {}
        defaults = {**self._current, **self.data}

        if user_input is not None:
            panic_th = int(user_input.get(CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD))
            solar_on = int(self.data.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON))
            
            if panic_th <= solar_on:
                errors["base"] = "panic_too_low"
            
            if not errors:
                self.data = {**self.data, **user_input}
                
                # Decide next step based on feature toggles
                if self.data.get(CONF_ENABLE_TEMP_MODULATION):
                    return await self.async_step_comfort()
                elif self.data.get(CONF_ENABLE_AUTO_SEASON):
                    return await self.async_step_auto_season()
                else:
                    # Create entry if no conditional steps
                    return self.async_create_entry(title="", data=self.data)

        schema = vol.Schema(
            {
                vol.Optional(CONF_ACTION_DELAY_SECONDS, default=int(defaults.get(CONF_ACTION_DELAY_SECONDS, DEFAULT_ACTION_DELAY_SECONDS))): _int_field(int(DEFAULT_ACTION_DELAY_SECONDS), minimum=0),
                vol.Optional(CONF_MANUAL_LOCK_SECONDS, default=int(defaults.get(CONF_MANUAL_LOCK_SECONDS, DEFAULT_MANUAL_LOCK_SECONDS))): _int_field(int(DEFAULT_MANUAL_LOCK_SECONDS), minimum=0),
                vol.Optional(CONF_SHORT_CYCLE_ON_SECONDS, default=int(defaults.get(CONF_SHORT_CYCLE_ON_SECONDS, DEFAULT_SHORT_CYCLE_ON_SECONDS))): _int_field(int(DEFAULT_SHORT_CYCLE_ON_SECONDS), minimum=0),
                vol.Optional(CONF_SHORT_CYCLE_OFF_SECONDS, default=int(defaults.get(CONF_SHORT_CYCLE_OFF_SECONDS, DEFAULT_SHORT_CYCLE_OFF_SECONDS))): _int_field(int(DEFAULT_SHORT_CYCLE_OFF_SECONDS), minimum=0),
                vol.Optional(CONF_PANIC_THRESHOLD, default=int(defaults.get(CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD))): _int_field(int(DEFAULT_PANIC_THRESHOLD), minimum=0),
                vol.Optional(CONF_PANIC_DELAY, default=int(defaults.get(CONF_PANIC_DELAY, DEFAULT_PANIC_DELAY))): _int_field(int(DEFAULT_PANIC_DELAY), minimum=0),
            }
        )

        return self.async_show_form(
            step_id="timing",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_comfort(self, user_input: dict[str, Any] | None = None):
        """Step 3: Comfort-Based Zone Control (conditional)."""
        errors: dict[str, str] = {}
        defaults = {**self._current, **self.data}

        if user_input is not None:
            zones = self.data.get(CONF_ZONES, [])
            zone_temp_sensors = user_input.get(CONF_ZONE_TEMP_SENSORS, [])
            
            # Validate zone_temp_sensors list length matches zones if provided
            if zone_temp_sensors and len(zone_temp_sensors) != len(zones):
                errors["base"] = "zone_temp_sensors_mismatch"
            
            if not errors:
                self.data = {**self.data, **user_input}
                
                # Next step: auto-season if enabled, else create entry
                if self.data.get(CONF_ENABLE_AUTO_SEASON):
                    return await self.async_step_auto_season()
                else:
                    return self.async_create_entry(title="", data=self.data)

        schema = vol.Schema(
            {
                vol.Optional(CONF_ZONE_TEMP_SENSORS, default=defaults.get(CONF_ZONE_TEMP_SENSORS, [])): selector({"entity": {"domain": "sensor", "device_class": ["temperature"], "multiple": True}}),
                vol.Optional(CONF_MAX_TEMP_WINTER, default=float(defaults.get(CONF_MAX_TEMP_WINTER, DEFAULT_MAX_TEMP_WINTER))): vol.Coerce(float),
                vol.Optional(CONF_MIN_TEMP_SUMMER, default=float(defaults.get(CONF_MIN_TEMP_SUMMER, DEFAULT_MIN_TEMP_SUMMER))): vol.Coerce(float),
            }
        )

        return self.async_show_form(
            step_id="comfort",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_auto_season(self, user_input: dict[str, Any] | None = None):
        """Step 4: Auto-Season Detection (conditional)."""
        errors: dict[str, str] = {}
        defaults = {**self._current, **self.data}

        if user_input is not None:
            heat_on = float(user_input.get(CONF_HEAT_ON_BELOW, DEFAULT_HEAT_ON_BELOW))
            heat_off = float(user_input.get(CONF_HEAT_OFF_ABOVE, DEFAULT_HEAT_OFF_ABOVE))
            cool_on = float(user_input.get(CONF_COOL_ON_ABOVE, DEFAULT_COOL_ON_ABOVE))
            cool_off = float(user_input.get(CONF_COOL_OFF_BELOW, DEFAULT_COOL_OFF_BELOW))
            very_cold = float(user_input.get(CONF_VERY_COLD_THRESHOLD, DEFAULT_VERY_COLD_THRESHOLD))
            chilly = float(user_input.get(CONF_CHILLY_THRESHOLD, DEFAULT_CHILLY_THRESHOLD))
            comfortable = float(user_input.get(CONF_COMFORTABLE_THRESHOLD, DEFAULT_COMFORTABLE_THRESHOLD))
            
            if heat_off <= heat_on:
                errors["base"] = "invalid_heat_hysteresis"
            elif cool_on <= cool_off:
                errors["base"] = "invalid_cool_hysteresis"
            elif not (very_cold < chilly < comfortable):
                errors["base"] = "invalid_bands"
            
            if not errors:
                self.data = {**self.data, **user_input}
                return self.async_create_entry(title="", data=self.data)

        schema = vol.Schema(
            {
                vol.Optional(CONF_OUTSIDE_SENSOR, default=defaults.get(CONF_OUTSIDE_SENSOR) or None): selector({"entity": {"domain": "sensor"}}),
                vol.Optional(CONF_HEAT_ON_BELOW, default=float(defaults.get(CONF_HEAT_ON_BELOW, DEFAULT_HEAT_ON_BELOW))): vol.Coerce(float),
                vol.Optional(CONF_HEAT_OFF_ABOVE, default=float(defaults.get(CONF_HEAT_OFF_ABOVE, DEFAULT_HEAT_OFF_ABOVE))): vol.Coerce(float),
                vol.Optional(CONF_COOL_ON_ABOVE, default=float(defaults.get(CONF_COOL_ON_ABOVE, DEFAULT_COOL_ON_ABOVE))): vol.Coerce(float),
                vol.Optional(CONF_COOL_OFF_BELOW, default=float(defaults.get(CONF_COOL_OFF_BELOW, DEFAULT_COOL_OFF_BELOW))): vol.Coerce(float),
                vol.Optional(CONF_VERY_COLD_THRESHOLD, default=float(defaults.get(CONF_VERY_COLD_THRESHOLD, DEFAULT_VERY_COLD_THRESHOLD))): vol.Coerce(float),
                vol.Optional(CONF_CHILLY_THRESHOLD, default=float(defaults.get(CONF_CHILLY_THRESHOLD, DEFAULT_CHILLY_THRESHOLD))): vol.Coerce(float),
                vol.Optional(CONF_COMFORTABLE_THRESHOLD, default=float(defaults.get(CONF_COMFORTABLE_THRESHOLD, DEFAULT_COMFORTABLE_THRESHOLD))): vol.Coerce(float),
                vol.Optional(CONF_MASTER_SWITCH_IN_OFFSEASON, default=bool(defaults.get(CONF_MASTER_SWITCH_IN_OFFSEASON, DEFAULT_MASTER_SWITCH_IN_OFFSEASON))): bool,
            }
        )

        return self.async_show_form(
            step_id="auto_season",
            data_schema=schema,
            errors=errors,
        )
