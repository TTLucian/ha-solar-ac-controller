# custom_components/solar_ac_controller/diagnostics.py
from __future__ import annotations

from typing import Any
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.diagnostics import async_redact_data

from .const import DOMAIN, CONF_SOLAR_SENSOR, CONF_GRID_SENSOR

# Keys to redact for privacy (e.g., if you had API keys)
TO_REDACT = {CONF_SOLAR_SENSOR, CONF_GRID_SENSOR}

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # 1. Start with Config & Options
    diag_data = {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "integration_enabled": coordinator.integration_enabled,
        "version": getattr(coordinator, "version", "unknown"),
    }

    # 2. Extract specific High-Value State
    # These represent the "State of Mind" of your AI
    import homeassistant.util.dt as dt_util
    def iso_ts(ts):
        if not ts:
            return None
        try:
            return dt_util.utc_from_timestamp(float(ts)).replace(microsecond=0).isoformat()
        except Exception:
            return None

    diag_data["logic_state"] = {
        "season_mode": getattr(coordinator, "season_mode", None),
        "ema_30s": getattr(coordinator, "ema_30s", None),
        "ema_5m": getattr(coordinator, "ema_5m", None),
        "panic_active": getattr(coordinator, "panic_active", None),
        "last_action": getattr(coordinator, "last_action", None),
        "note": getattr(coordinator, "note", None),
        "required_export_source": getattr(coordinator, "required_export_source", None),
        "last_action_started_at": iso_ts(getattr(coordinator, "last_action_start_ts", None)),
        "last_action_start_ts": getattr(coordinator, "last_action_start_ts", None),
        "last_panic_at": iso_ts(getattr(coordinator, "last_panic_ts", None)),
        "last_panic_ts": getattr(coordinator, "last_panic_ts", None),
    }

    # 3. Learning Data (The most important part for troubleshooting)
    diag_data["learned_data"] = {
        "learned_power": getattr(coordinator, "learned_power", None),
        "samples": getattr(coordinator, "samples", None),
    }

    # 4. Zone State Map (with friendly names)
    zone_info = {}
    for zone in getattr(coordinator, "config", {}).get("zones", []):
        st = getattr(coordinator.hass.states, "get", lambda x: None)(zone)
        friendly = st.attributes.get("friendly_name") if st and hasattr(st, "attributes") else None
        zone_info[zone] = {
            "friendly_name": friendly,
            "last_state": getattr(coordinator, "zone_last_state", {}).get(zone),
            "locked_until": getattr(coordinator, "zone_manual_lock_until", {}).get(zone),
            "is_locked": getattr(coordinator, "zone_manager").is_locked(zone) if getattr(coordinator, "zone_manager", None) else None,
            "is_short_cycling": getattr(coordinator, "zone_manager").is_short_cycling(zone) if getattr(coordinator, "zone_manager", None) else None,
            "current_temp": getattr(coordinator, "zone_current_temps", {}).get(zone),
        }
    diag_data["zones"] = zone_info

    return diag_data
