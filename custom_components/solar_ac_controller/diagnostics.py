from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util

from .const import DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
):
    """Return diagnostics for a config entry."""

    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    now = dt_util.utcnow().timestamp()

    # Compute active zones
    active = []
    for z in coordinator.config["zones"]:
        st = coordinator.hass.states.get(z)
        if st and st.state in ("heat", "on"):
            active.append(z)

    # Compute next zone
    next_zone = None
    for z in coordinator.config["zones"]:
        st = coordinator.hass.states.get(z)
        if not st or st.state not in ("heat", "on"):
            lock = coordinator.zone_manual_lock_until.get(z)
            if lock and lock > now:
                continue
            next_zone = z
            break

    return {
        "timestamp": now,
        "config": coordinator.config,
        "samples": coordinator.samples,
        "learned_power": coordinator.learned_power,
        "learning_active": coordinator.learning_active,
        "learning_zone": coordinator.learning_zone,
        "learning_start_time": coordinator.learning_start_time,
        "ac_power_before": coordinator.ac_power_before,
        "ema_30s": coordinator.ema_30s,
        "ema_5m": coordinator.ema_5m,
        "last_action": coordinator.last_action,
        "zone_last_changed": coordinator.zone_last_changed,
        "zone_last_state": coordinator.zone_last_state,
        "zone_manual_lock_until": coordinator.zone_manual_lock_until,
        "panic_threshold": coordinator.panic_threshold,
        "panic_delay": coordinator.panic_delay,
        "active_zones": active,
        "next_zone": next_zone,
    }
