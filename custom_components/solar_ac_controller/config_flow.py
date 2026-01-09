from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

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
)

DEFAULT_ADD_CONFIDENCE = 25
DEFAULT_REMOVE_CONFIDENCE = 10


def _ensure_list(value: Any) -> list[str]:
    """Ensure zones are stored as a list of strings."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


class SolarACConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of the Solar AC Controller."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Initial configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            zones = _ensure_list(user_input.get(CONF_ZONES, []))
            if not zones:
                errors["base"] = "no_zones"
            else:
                data = dict(user_input)
                data[CONF_ZONES] = zones
                return self.async_create_entry(
                    title="Solar AC Controller",
                    data=data,
                )

        schema = vol.Schema(
            {
                # Core sensors
                vol.Required(CONF_SOLAR_SENSOR): str,
                vol.Required(CONF_GRID_SENSOR): str,
                vol.Required(CONF_AC_POWER_SENSOR): str,
                vol.Required(CONF_AC_SWITCH): str,

                # Zones (comma-separated)
                vol.Required(CONF_ZONES): str,

                # Thresholds
                vol.Optional(CONF_SOLAR_THRESHOLD_ON, default=1200): int,
                vol.Optional(CONF_SOLAR_THRESHOLD_OFF, default=800): int,

                # Safety / panic
                vol.Optional(CONF_PANIC_THRESHOLD, default=1500): int,
                vol.Optional(CONF_PANIC_DELAY, default=30): int,
                vol.Optional(CONF_MANUAL_LOCK_SECONDS, default=1200): int,
                vol.Optional(CONF_SHORT_CYCLE_ON_SECONDS, default=1200): int,
                vol.Optional(CONF_SHORT_CYCLE_OFF_SECONDS, default=1200): int,
                vol.Optional(CONF_ACTION_DELAY_SECONDS, default=3): int,

                # Unified confidence thresholds
                vol.Optional(CONF_ADD_CONFIDENCE, default=DEFAULT_ADD_CONFIDENCE): int,
                vol.Optional(CONF_REMOVE_CONFIDENCE, default=DEFAULT_REMOVE_CONFIDENCE): int,

                # Initial learned power
                vol.Required(CONF_INITIAL_LEARNED_POWER, default=1200): vol.Coerce(int),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_import(self, user_input: dict[str, Any]):
        """Handle YAML import."""
        return await self.async_step_user(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Return the options flow handler."""
        return SolarACOptionsFlowHandler(config_entry)


class SolarACOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle runtime configuration changes."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        self.entry = config_entry

    @property
    def _current(self) -> dict[str, Any]:
        """Return current effective config (options override data)."""
        return {**self.entry.data, **self.entry.options}

    async def async_step_init(self, user_input=None):
        """Single expert-style options form."""
        errors: dict[str, str] = {}
        current = self._current

        if user_input is not None:
            solar_on = user_input.get(CONF_SOLAR_THRESHOLD_ON, 1200)
            solar_off = user_input.get(CONF_SOLAR_THRESHOLD_OFF, 800)
            panic_th = user_input.get(CONF_PANIC_THRESHOLD, 2500)
            zones = _ensure_list(user_input.get(CONF_ZONES, []))

            if not zones:
                errors["base"] = "no_zones"
            elif solar_off >= solar_on:
                errors["base"] = "invalid_solar_hysteresis"
            elif panic_th <= solar_on:
                errors["base"] = "panic_too_low"
            else:
                new_options = {
                    CONF_SOLAR_SENSOR: user_input[CONF_SOLAR_SENSOR],
                    CONF_GRID_SENSOR: user_input[CONF_GRID_SENSOR],
                    CONF_AC_POWER_SENSOR: user_input[CONF_AC_POWER_SENSOR],
                    CONF_AC_SWITCH: user_input[CONF_AC_SWITCH],
                    CONF_ZONES: zones,
                    CONF_SOLAR_THRESHOLD_ON: solar_on,
                    CONF_SOLAR_THRESHOLD_OFF: solar_off,
                    CONF_PANIC_THRESHOLD: panic_th,
                    CONF_PANIC_DELAY: user_input.get(CONF_PANIC_DELAY, 30),
                    CONF_MANUAL_LOCK_SECONDS: user_input.get(CONF_MANUAL_LOCK_SECONDS, 1200),
                    CONF_SHORT_CYCLE_ON_SECONDS: user_input.get(CONF_SHORT_CYCLE_ON_SECONDS, 1200),
                    CONF_SHORT_CYCLE_OFF_SECONDS: user_input.get(CONF_SHORT_CYCLE_OFF_SECONDS, 1200),
                    CONF_ACTION_DELAY_SECONDS: user_input.get(CONF_ACTION_DELAY_SECONDS, 3),

                    # Unified confidence thresholds
                    CONF_ADD_CONFIDENCE: user_input.get(CONF_ADD_CONFIDENCE, DEFAULT_ADD_CONFIDENCE),
                    CONF_REMOVE_CONFIDENCE: user_input.get(CONF_REMOVE_CONFIDENCE, DEFAULT_REMOVE_CONFIDENCE),

                    # Initial learned power (now editable)
                    CONF_INITIAL_LEARNED_POWER: user_input.get(CONF_INITIAL_LEARNED_POWER, 1200),
                }
                return self.async_create_entry(title="", data=new_options)

            current = user_input

        return self._show_main_form(current, errors)

    def _show_main_form(self, data: dict[str, Any], errors: dict[str, str]):
        """Render main options form."""
        zones = data.get(CONF_ZONES, [])
        if isinstance(zones, list):
            zones_str = ", ".join(zones)
        else:
            zones_str = str(zones) if zones else ""

        schema = vol.Schema(
            {
                # Sensors
                vol.Required(CONF_SOLAR_SENSOR, default=data.get(CONF_SOLAR_SENSOR, "")): str,
                vol.Required(CONF_GRID_SENSOR, default=data.get(CONF_GRID_SENSOR, "")): str,
                vol.Required(CONF_AC_POWER_SENSOR, default=data.get(CONF_AC_POWER_SENSOR, "")): str,
                vol.Required(CONF_AC_SWITCH, default=data.get(CONF_AC_SWITCH, "")): str,

                # Zones
                vol.Required(CONF_ZONES, default=zones_str): str,

                # Thresholds
                vol.Optional(CONF_SOLAR_THRESHOLD_ON, default=data.get(CONF_SOLAR_THRESHOLD_ON, 1200)): int,
                vol.Optional(CONF_SOLAR_THRESHOLD_OFF, default=data.get(CONF_SOLAR_THRESHOLD_OFF, 800)): int,

                # Advanced / safety
                vol.Optional(CONF_PANIC_THRESHOLD, default=data.get(CONF_PANIC_THRESHOLD, 1500)): int,
                vol.Optional(CONF_PANIC_DELAY, default=data.get(CONF_PANIC_DELAY, 30)): int,
                vol.Optional(CONF_MANUAL_LOCK_SECONDS, default=data.get(CONF_MANUAL_LOCK_SECONDS, 1200)): int,
                vol.Optional(CONF_SHORT_CYCLE_ON_SECONDS, default=data.get(CONF_SHORT_CYCLE_ON_SECONDS, 1200)): int,
                vol.Optional(CONF_SHORT_CYCLE_OFF_SECONDS, default=data.get(CONF_SHORT_CYCLE_OFF_SECONDS, 1200)): int,
                vol.Optional(CONF_ACTION_DELAY_SECONDS, default=data.get(CONF_ACTION_DELAY_SECONDS, 3)): int,

                # Unified confidence thresholds
                vol.Optional(CONF_ADD_CONFIDENCE, default=data.get(CONF_ADD_CONFIDENCE, DEFAULT_ADD_CONFIDENCE)): int,
                vol.Optional(CONF_REMOVE_CONFIDENCE, default=data.get(CONF_REMOVE_CONFIDENCE, DEFAULT_REMOVE_CONFIDENCE)): int,

                # Initial learned power (editable)
                vol.Optional(CONF_INITIAL_LEARNED_POWER, default=data.get(CONF_INITIAL_LEARNED_POWER, 1200)): int,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
