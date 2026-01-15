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
)


def _int_field(default: int, minimum: int = 0) -> vol.All:
    # vol.Default does not exist; set default in the schema, not here
    return vol.All(vol.Coerce(int), vol.Range(min=minimum))


class SolarACConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of the Solar AC Controller."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Initial configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            zones = user_input.get(CONF_ZONES, [])
            if not zones:
                errors["base"] = "no_zones"
            else:
                # Normalize zones to list of strings
                normalized = {**user_input}
                normalized[CONF_ZONES] = list(zones)
                return self.async_create_entry(
                    title="Solar AC Controller",
                    data=normalized,
                )

        # Group advanced options
        advanced_schema = {
            vol.Optional(CONF_ADD_CONFIDENCE, default=int(DEFAULT_ADD_CONFIDENCE)): _int_field(int(DEFAULT_ADD_CONFIDENCE), minimum=0),
            vol.Optional(CONF_REMOVE_CONFIDENCE, default=int(DEFAULT_REMOVE_CONFIDENCE)): _int_field(int(DEFAULT_REMOVE_CONFIDENCE), minimum=0),
            vol.Optional(CONF_ENABLE_DIAGNOSTICS, default=False): bool,
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_SOLAR_SENSOR): selector({"entity": {"domain": "sensor"}}),
                vol.Required(CONF_GRID_SENSOR): selector({"entity": {"domain": "sensor"}}),
                vol.Required(CONF_AC_POWER_SENSOR): selector({"entity": {"domain": "sensor"}}),

                vol.Optional(CONF_AC_SWITCH, default=""): selector({"entity": {"domain": "switch"}}),

                vol.Required(CONF_ZONES): selector({
                    "entity": {"domain": ["climate", "switch", "fan"], "multiple": True}
                }),

                vol.Optional(CONF_SOLAR_THRESHOLD_ON, default=int(DEFAULT_SOLAR_THRESHOLD_ON)): _int_field(int(DEFAULT_SOLAR_THRESHOLD_ON), minimum=0),
                vol.Optional(CONF_SOLAR_THRESHOLD_OFF, default=int(DEFAULT_SOLAR_THRESHOLD_OFF)): _int_field(int(DEFAULT_SOLAR_THRESHOLD_OFF), minimum=0),

                vol.Optional(CONF_PANIC_THRESHOLD, default=int(DEFAULT_PANIC_THRESHOLD)): _int_field(int(DEFAULT_PANIC_THRESHOLD), minimum=0),
                vol.Optional(CONF_PANIC_DELAY, default=int(DEFAULT_PANIC_DELAY)): _int_field(int(DEFAULT_PANIC_DELAY), minimum=0),
                vol.Optional(CONF_MANUAL_LOCK_SECONDS, default=int(DEFAULT_MANUAL_LOCK_SECONDS)): _int_field(int(DEFAULT_MANUAL_LOCK_SECONDS), minimum=0),
                vol.Optional(CONF_SHORT_CYCLE_ON_SECONDS, default=int(DEFAULT_SHORT_CYCLE_ON_SECONDS)): _int_field(int(DEFAULT_SHORT_CYCLE_ON_SECONDS), minimum=0),
                vol.Optional(CONF_SHORT_CYCLE_OFF_SECONDS, default=int(DEFAULT_SHORT_CYCLE_OFF_SECONDS)): _int_field(int(DEFAULT_SHORT_CYCLE_OFF_SECONDS), minimum=0),
                vol.Optional(CONF_ACTION_DELAY_SECONDS, default=int(DEFAULT_ACTION_DELAY_SECONDS)): _int_field(int(DEFAULT_ACTION_DELAY_SECONDS), minimum=0),

                vol.Required(CONF_INITIAL_LEARNED_POWER, default=int(DEFAULT_INITIAL_LEARNED_POWER)): vol.All(vol.Coerce(int), vol.Range(min=0)),
            }
        )
        # Add advanced options as a separate group (shown after main fields)
        schema = schema.extend(advanced_schema)

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "add_confidence": "Minimum confidence required to add a new zone.",
                "remove_confidence": "Minimum negative confidence required to remove a zone.",
                "enable_diagnostics": "Enable a sensor with full controller state for debugging.",
            },
        )

    async def async_step_import(self, user_input: dict[str, Any]):
        return await self.async_step_user(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return SolarACOptionsFlowHandler(config_entry)


class SolarACOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle runtime configuration changes."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        self.entry = config_entry

    @property
    def _current(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        current = self._current

        if user_input is not None:
            solar_on = int(user_input.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON))
            solar_off = int(user_input.get(CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF))
            panic_th = int(user_input.get(CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD))
            zones = user_input.get(CONF_ZONES, [])

            if not zones:
                errors["base"] = "no_zones"
            elif solar_off >= solar_on:
                errors["base"] = "invalid_solar_hysteresis"
            elif panic_th <= solar_on:
                errors["base"] = "panic_too_low"
            else:
                # Normalize and coerce numeric options
                new_options = {
                    CONF_SOLAR_SENSOR: user_input[CONF_SOLAR_SENSOR],
                    CONF_GRID_SENSOR: user_input[CONF_GRID_SENSOR],
                    CONF_AC_POWER_SENSOR: user_input[CONF_AC_POWER_SENSOR],
                    CONF_AC_SWITCH: user_input.get(CONF_AC_SWITCH, ""),
                    CONF_ZONES: list(zones),
                    CONF_SOLAR_THRESHOLD_ON: solar_on,
                    CONF_SOLAR_THRESHOLD_OFF: solar_off,
                    CONF_PANIC_THRESHOLD: panic_th,
                    CONF_PANIC_DELAY: int(user_input.get(CONF_PANIC_DELAY, DEFAULT_PANIC_DELAY)),
                    CONF_MANUAL_LOCK_SECONDS: int(user_input.get(CONF_MANUAL_LOCK_SECONDS, DEFAULT_MANUAL_LOCK_SECONDS)),
                    CONF_SHORT_CYCLE_ON_SECONDS: int(user_input.get(CONF_SHORT_CYCLE_ON_SECONDS, DEFAULT_SHORT_CYCLE_ON_SECONDS)),
                    CONF_SHORT_CYCLE_OFF_SECONDS: int(user_input.get(CONF_SHORT_CYCLE_OFF_SECONDS, DEFAULT_SHORT_CYCLE_OFF_SECONDS)),
                    CONF_ACTION_DELAY_SECONDS: int(user_input.get(CONF_ACTION_DELAY_SECONDS, DEFAULT_ACTION_DELAY_SECONDS)),
                    CONF_ADD_CONFIDENCE: int(user_input.get(CONF_ADD_CONFIDENCE, DEFAULT_ADD_CONFIDENCE)),
                    CONF_REMOVE_CONFIDENCE: int(user_input.get(CONF_REMOVE_CONFIDENCE, DEFAULT_REMOVE_CONFIDENCE)),

                    # Diagnostics toggle is saved in options
                    CONF_ENABLE_DIAGNOSTICS: bool(user_input.get(CONF_ENABLE_DIAGNOSTICS, False)),

                    CONF_INITIAL_LEARNED_POWER: int(user_input.get(CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER)),
                }
                return self.async_create_entry(title="", data=new_options)

            current = user_input

        return self._show_main_form(current, errors)

    def _show_main_form(self, data: dict[str, Any], errors: dict[str, str]):
        schema = vol.Schema(
            {
                vol.Required(CONF_SOLAR_SENSOR, default=data.get(CONF_SOLAR_SENSOR)): selector({"entity": {"domain": "sensor"}}),
                vol.Required(CONF_GRID_SENSOR, default=data.get(CONF_GRID_SENSOR)): selector({"entity": {"domain": "sensor"}}),
                vol.Required(CONF_AC_POWER_SENSOR, default=data.get(CONF_AC_POWER_SENSOR)): selector({"entity": {"domain": "sensor"}}),

                vol.Optional(CONF_AC_SWITCH, default=data.get(CONF_AC_SWITCH, "")): selector({"entity": {"domain": "switch"}}),

                vol.Required(CONF_ZONES, default=data.get(CONF_ZONES, [])): selector({
                    "entity": {"domain": selector({"select": {"options": ["climate", "switch", "fan"]}})["select"]["options"], "multiple": True}
                }),

                vol.Optional(CONF_SOLAR_THRESHOLD_ON, default=data.get(CONF_SOLAR_THRESHOLD_ON, int(DEFAULT_SOLAR_THRESHOLD_ON))): _int_field(int(DEFAULT_SOLAR_THRESHOLD_ON), minimum=0),
                vol.Optional(CONF_SOLAR_THRESHOLD_OFF, default=data.get(CONF_SOLAR_THRESHOLD_OFF, int(DEFAULT_SOLAR_THRESHOLD_OFF))): _int_field(int(DEFAULT_SOLAR_THRESHOLD_OFF), minimum=0),

                vol.Optional(CONF_PANIC_THRESHOLD, default=data.get(CONF_PANIC_THRESHOLD, int(DEFAULT_PANIC_THRESHOLD))): _int_field(int(DEFAULT_PANIC_THRESHOLD), minimum=0),
                vol.Optional(CONF_PANIC_DELAY, default=data.get(CONF_PANIC_DELAY, int(DEFAULT_PANIC_DELAY))): _int_field(int(DEFAULT_PANIC_DELAY), minimum=0),
                vol.Optional(CONF_MANUAL_LOCK_SECONDS, default=data.get(CONF_MANUAL_LOCK_SECONDS, int(DEFAULT_MANUAL_LOCK_SECONDS))): _int_field(int(DEFAULT_MANUAL_LOCK_SECONDS), minimum=0),
                vol.Optional(CONF_SHORT_CYCLE_ON_SECONDS, default=data.get(CONF_SHORT_CYCLE_ON_SECONDS, int(DEFAULT_SHORT_CYCLE_ON_SECONDS))): _int_field(int(DEFAULT_SHORT_CYCLE_ON_SECONDS), minimum=0),
                vol.Optional(CONF_SHORT_CYCLE_OFF_SECONDS, default=data.get(CONF_SHORT_CYCLE_OFF_SECONDS, int(DEFAULT_SHORT_CYCLE_OFF_SECONDS))): _int_field(int(DEFAULT_SHORT_CYCLE_OFF_SECONDS), minimum=0),
                vol.Optional(CONF_ACTION_DELAY_SECONDS, default=data.get(CONF_ACTION_DELAY_SECONDS, int(DEFAULT_ACTION_DELAY_SECONDS))): _int_field(int(DEFAULT_ACTION_DELAY_SECONDS), minimum=0),

                vol.Optional(CONF_ADD_CONFIDENCE, default=data.get(CONF_ADD_CONFIDENCE, int(DEFAULT_ADD_CONFIDENCE))): _int_field(int(DEFAULT_ADD_CONFIDENCE), minimum=0),
                vol.Optional(CONF_REMOVE_CONFIDENCE, default=data.get(CONF_REMOVE_CONFIDENCE, int(DEFAULT_REMOVE_CONFIDENCE))): _int_field(int(DEFAULT_REMOVE_CONFIDENCE), minimum=0),

                # Diagnostics toggle included in options UI
                vol.Optional(CONF_ENABLE_DIAGNOSTICS, default=data.get(CONF_ENABLE_DIAGNOSTICS, False)): bool,

                vol.Optional(CONF_INITIAL_LEARNED_POWER, default=data.get(CONF_INITIAL_LEARNED_POWER, int(DEFAULT_INITIAL_LEARNED_POWER))): vol.All(vol.Coerce(int), vol.Range(min=0)),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
