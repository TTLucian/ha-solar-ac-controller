from __future__ import annotations

import voluptuous as vol
from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant

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
)


def _ensure_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


class SolarACConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of the Solar AC Controller."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Initial configuration step."""
        if user_input is not None:
            # Basic sanity checks
            zones = _ensure_list(user_input.get(CONF_ZONES, []))
            if not zones:
                errors = {"base": "no_zones"}
            else:
                return self.async_create_entry(
                    title="Solar AC Controller",
                    data={
                        **user_input,
                        CONF_ZONES: zones,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_SOLAR_SENSOR): str,
                vol.Required(CONF_GRID_SENSOR): str,
                vol.Required(CONF_AC_POWER_SENSOR): str,
                vol.Required(CONF_AC_SWITCH): str,
                vol.Required(
                    CONF_ZONES,
                    description="Comma-separated list, in priority order",
                ): str,
                vol.Optional(CONF_SOLAR_THRESHOLD_ON, default=1200): int,
                vol.Optional(CONF_SOLAR_THRESHOLD_OFF, default=800): int,
                vol.Optional(CONF_PANIC_THRESHOLD, default=2500): int,
                vol.Optional(CONF_PANIC_DELAY, default=10): int,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            description_placeholders={
                "info": (
                    "The controller will add zones in the order you define here. "
                    "It uses solar, grid, and learned AC power to decide when to turn "
                    "zones on or off. All values can be adjusted later from Options."
                )
            },
        )

    async def async_step_import(self, user_input):
        """Support YAML import."""
        return await self.async_step_user(user_input)


class SolarACOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle runtime configuration changes."""

    def __init__(self, hass: HomeAssistant, config_entry: config_entries.ConfigEntry):
        self.hass = hass
        self.entry = config_entry

    @property
    def _current(self) -> dict[str, Any]:
        # options override data
        return {**self.entry.data, **self.entry.options}

    async def async_step_init(self, user_input=None):
        """Entry point for the options flow."""
        # Single consolidated form (simpler, less clicking)
        data = self._current

        if user_input is not None:
            # Validation
            errors: dict[str, str] = {}

            solar_on = user_input.get(CONF_SOLAR_THRESHOLD_ON, 1200)
            solar_off = user_input.get(CONF_SOLAR_THRESHOLD_OFF, 800)
            panic_th = user_input.get(CONF_PANIC_THRESHOLD, 2500)

            if solar_off >= solar_on:
                errors["base"] = "invalid_solar_hysteresis"
            elif panic_th <= solar_on:
                errors["base"] = "panic_too_low"
            else:
                zones = _ensure_list(user_input.get(CONF_ZONES, []))
                if not zones:
                    errors["base"] = "no_zones"
                else:
                    # Commit – store zones as list, everything else as-is
                    new_options = {
                        CONF_SOLAR_SENSOR: user_input[CONF_SOLAR_SENSOR],
                        CONF_GRID_SENSOR: user_input[CONF_GRID_SENSOR],
                        CONF_AC_POWER_SENSOR: user_input[CONF_AC_POWER_SENSOR],
                        CONF_AC_SWITCH: user_input[CONF_AC_SWITCH],
                        CONF_ZONES: zones,
                        CONF_SOLAR_THRESHOLD_ON: solar_on,
                        CONF_SOLAR_THRESHOLD_OFF: solar_off,
                        CONF_PANIC_THRESHOLD: panic_th,
                        CONF_PANIC_DELAY: user_input.get(CONF_PANIC_DELAY, 10),
                    }
                    return self.async_create_entry(title="", data=new_options)

            # Show form again if errors
            return await self._show_main_form(data=user_input, errors=errors)

        # Initial display
        return await self._show_main_form(data=data, errors={})

    async def _show_main_form(self, data: dict[str, Any], errors: dict[str, str]):
        """Render the main options form."""
        zones = data.get(CONF_ZONES, [])
        if isinstance(zones, list):
            zones_str = ", ".join(zones)
        else:
            zones_str = str(zones)

        schema = vol.Schema(
            {
                # Sensors
                vol.Required(CONF_SOLAR_SENSOR, default=data.get(CONF_SOLAR_SENSOR, "")): str,
                vol.Required(CONF_GRID_SENSOR, default=data.get(CONF_GRID_SENSOR, "")): str,
                vol.Required(
                    CONF_AC_POWER_SENSOR, default=data.get(CONF_AC_POWER_SENSOR, "")
                ): str,
                vol.Required(CONF_AC_SWITCH, default=data.get(CONF_AC_SWITCH, "")): str,
                # Zones
                vol.Required(
                    CONF_ZONES,
                    description="Comma-separated, in the order you want zones to be activated",
                    default=zones_str,
                ): str,
                # Thresholds
                vol.Optional(
                    CONF_SOLAR_THRESHOLD_ON,
                    default=data.get(CONF_SOLAR_THRESHOLD_ON, 1200),
                ): int,
                vol.Optional(
                    CONF_SOLAR_THRESHOLD_OFF,
                    default=data.get(CONF_SOLAR_THRESHOLD_OFF, 800),
                ): int,
                # Advanced / safety
                vol.Optional(
                    CONF_PANIC_THRESHOLD,
                    default=data.get(CONF_PANIC_THRESHOLD, 2500),
                ): int,
                vol.Optional(
                    CONF_PANIC_DELAY,
                    default=data.get(CONF_PANIC_DELAY, 10),
                ): int,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "info": (
                    "Sensors:\n"
                    "- Solar: positive when exporting, negative when importing.\n"
                    "- Grid: positive when importing from grid, negative when exporting.\n\n"
                    "Zones:\n"
                    "- Enter climate entities as a comma-separated list.\n"
                    "- Order = activation priority. First in list turns on first.\n\n"
                    "Thresholds:\n"
                    "- Solar ON: minimum stable export before adding a new zone.\n"
                    "- Solar OFF: below this, the master switch may shut AC down.\n\n"
                    "Advanced:\n"
                    "- Panic threshold: grid import level that triggers emergency shedding.\n"
                    "- Panic delay: seconds to wait before actually shedding, to ignore spikes."
                )
            },
        )
