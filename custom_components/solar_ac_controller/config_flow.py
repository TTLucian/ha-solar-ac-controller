from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

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
    """Handle the Solar AC Controller config flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Initial configuration step."""
        if user_input is not None:
            return self.async_create_entry(title="Solar AC Controller", data=user_input)

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
                vol.Required(CONF_SOLAR_THRESHOLD_ON, default=1200): selector.selector(
                    {"number": {"min": 0, "max": 5000, "step": 50}}
                ),
                vol.Required(CONF_SOLAR_THRESHOLD_OFF, default=800): selector.selector(
                    {"number": {"min": 0, "max": 5000, "step": 50}}
                ),
                vol.Required(CONF_PANIC_THRESHOLD, default=2500): selector.selector(
                    {"number": {"min": 500, "max": 8000, "step": 50}}
                ),
                vol.Required(CONF_PANIC_DELAY, default=10): selector.selector(
                    {"number": {"min": 0, "max": 60, "step": 1}}
                ),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_import(self, user_input):
        """Support YAML import (legacy)."""
        return await self.async_step_user(user_input)
