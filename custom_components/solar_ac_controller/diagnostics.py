from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN
from .helpers import build_diagnostics

_LOGGER = logging.getLogger(__name__)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return unified diagnostics for a config entry.

    This returns the same diagnostics payload used by the diagnostics sensor
    and ensures we read the authoritative coordinator state (including
    the integration version and cooling support).
    """
    try:
        entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if not entry_data:
            _LOGGER.error("Diagnostics requested for unknown config entry %s", entry.entry_id)
            return {}

        coordinator = entry_data.get("coordinator")
        if not coordinator:
            _LOGGER.error("No coordinator available for diagnostics for entry %s", entry.entry_id)
            return {}

        return build_diagnostics(coordinator)
    except Exception as exc:  # Defensive: never raise from diagnostics endpoint
        _LOGGER.exception("Failed to build diagnostics for entry %s: %s", entry.entry_id, exc)
        return {"error": str(exc)}
