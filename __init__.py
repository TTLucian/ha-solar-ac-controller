from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .const import DOMAIN

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up from YAML (not used for config flow, but keep for compatibility)."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Solar AC Controller from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "solar_sensor": entry.data.get("solar_sensor"),
        "grid_sensor": entry.data.get("grid_sensor"),
        "ac_power_sensor": entry.data.get("ac_power_sensor"),
        "ac_switch": entry.data.get("ac_switch"),
        "zones": entry.data.get("zones", []),
    }

    # Placeholder: later you can register platforms or services here
    # e.g. hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True
