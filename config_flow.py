from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN, CONF_SOLAR_SENSOR, CONF_GRID_SENSOR, CONF_AC_POWER_SENSOR, CONF_AC_SWITCH, CONF_ZONES


class SolarACConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Solar AC Controller."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""
        if user_input is not None:
            return self.async_create_entry(
                title="Solar AC Controller",
                data=user_input,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_SOLAR_SENSOR): selector.selector(
                    {"entity": {"domain": "sensor"}}
                ),
                vol.Required(CONF_GRID_SENSOR): selector.selector(
                    {"entity": {"domain": "sensor"}}
                ),
                vol.Required(CONF_AC_POWER_SENSOR): selector.selector(
                    {"entity": {"domain": "sensor"}}
                ),
                vol.Required(CONF_AC_SWITCH): selector.selector(
                    {"entity": {"domain": "switch"}}
                ),
                vol.Required(CONF_ZONES): selector.selector(
                    {"entity": {"multiple": True, "domain": "climate"}}
                ),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema)

    @callback
    def async_get_options_flow(self, config_entry):
        return SolarACOptionsFlow(config_entry)


class SolarACOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Solar AC."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        data = self.config_entry.data
        schema = vol.Schema(
            {
                vol.Required(CONF_ZONES, default=data.get(CONF_ZONES, [])): selector.selector(
                    {"entity": {"multiple": True, "domain": "climate"}}
                )
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
