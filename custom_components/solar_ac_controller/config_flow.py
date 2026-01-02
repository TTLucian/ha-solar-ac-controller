from __future__ import annotations

import voluptuous as vol
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


class SolarACConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of the Solar AC Controller."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Initial configuration step."""
        if user_input is not None:
            return self.async_create_entry(
                title="Solar AC Controller",
                data=user_input,
            )

        schema = vol.Schema({
            vol.Required(CONF_SOLAR_SENSOR): str,
            vol.Required(CONF_GRID_SENSOR): str,
            vol.Required(CONF_AC_POWER_SENSOR): str,
            vol.Required(CONF_AC_SWITCH): str,
            vol.Required(CONF_ZONES): [str],

            vol.Optional(CONF_SOLAR_THRESHOLD_ON, default=1200): int,
            vol.Optional(CONF_SOLAR_THRESHOLD_OFF, default=800): int,

            vol.Optional(CONF_PANIC_THRESHOLD, default=2500): int,
            vol.Optional(CONF_PANIC_DELAY, default=10): int,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            description_placeholders={
                "info": (
                    "This integration automatically manages your AC zones "
                    "based on solar production, grid import/export, and learned "
                    "compressor behavior. You can adjust all settings later "
                    "from the Options menu."
                )
            },
        )

    async def async_step_import(self, user_input):
        """Support YAML import."""
        return await self.async_step_user(user_input)

    async def async_step_options(self, user_input=None):
        """Redirect to options flow."""
        return await SolarACOptionsFlowHandler(self.hass, self.config_entry).async_step_init()


class SolarACOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle runtime configuration changes."""

    def __init__(self, hass: HomeAssistant, entry: config_entries.ConfigEntry):
        self.hass = hass
        self.entry = entry

    async def async_step_init(self, user_input=None):
        """Main options menu."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        data = self.entry.data

        schema = vol.Schema({
            vol.Required(CONF_SOLAR_SENSOR, default=data.get(CONF_SOLAR_SENSOR)): str,
            vol.Required(CONF_GRID_SENSOR, default=data.get(CONF_GRID_SENSOR)): str,
            vol.Required(CONF_AC_POWER_SENSOR, default=data.get(CONF_AC_POWER_SENSOR)): str,
            vol.Required(CONF_AC_SWITCH, default=data.get(CONF_AC_SWITCH)): str,

            vol.Required(CONF_ZONES, default=data.get(CONF_ZONES)): [str],

            vol.Optional(CONF_SOLAR_THRESHOLD_ON, default=data.get(CONF_SOLAR_THRESHOLD_ON, 1200)): int,
            vol.Optional(CONF_SOLAR_THRESHOLD_OFF, default=data.get(CONF_SOLAR_THRESHOLD_OFF, 800)): int,

            vol.Optional(CONF_PANIC_THRESHOLD, default=data.get(CONF_PANIC_THRESHOLD, 2500)): int,
            vol.Optional(CONF_PANIC_DELAY, default=data.get(CONF_PANIC_DELAY, 10)): int,
        })

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={
                "info": (
                    "You can safely adjust all settings here. "
                    "Zone order determines activation priority. "
                    "Thresholds control when zones are added or removed. "
                    "Panic settings protect your inverter from overload."
                )
            },
        )
