# custom_components/solar_ac_controller/diagnostics.py
from __future__ import annotations

from typing import Any
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    data: dict[str, Any] = {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "data": dict(entry.data),
        "options": dict(entry.options),
    }

    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("coordinator")

    if coordinator is not None:
        # Dump coordinator attributes
        for attr in dir(coordinator):
            if attr.startswith("_"):
                continue
            try:
                val = getattr(coordinator, attr)
                # Convert non-serializable objects to string
                if isinstance(val, (dict, list, str, int, float, bool)) or val is None:
                    data[attr] = val
                else:
                    data[attr] = str(val)
            except Exception:
                data[attr] = "<error>"

    return data
