from __future__ import annotations

from homeassistant.util import dt as dt_util


def build_diagnostics(coordinator):
    """Return a unified diagnostics structure for both sensor + HA diagnostics."""

    now = dt_util.utcnow().timestamp()

    # Active zones
    active_zones = [
        z for z in coordinator.config.get("zones", [])
        if (st := coordinator.hass.states.get(z)) and st.state in ("heat", "on")
    ]

    # Panic cooldown
    panic_cooldown_active = False
    if coordinator.last_panic_ts:
        panic_cooldown_active = (now - coordinator.last_panic_ts) < 120

    return {
        "version": coordinator.config_entry.data.get("version"),
        "timestamp": dt_util.utcnow().isoformat(),
        "config": coordinator.config,

        # Learning
        "samples": coordinator.samples,
        "learned_power": coordinator.learned_power,
        "learning_active": coordinator.learning_active,
        "learning_zone": coordinator.learning_zone,
        "learning_start_time": coordinator.learning_start_time,
        "ac_power_before": coordinator.ac_power_before,

        # EMA
        "ema_30s": coordinator.ema_30s,
        "ema_5m": coordinator.ema_5m,

        # Decision state
        "last_action": coordinator.last_action,
        "next_zone": coordinator.next_zone,
        "last_zone": coordinator.last_zone,
        "required_export": coordinator.required_export,
        "export_margin": coordinator.export_margin,

        # Zones
        "active_zones": active_zones,
        "zone_last_changed": coordinator.zone_last_changed,
        "zone_last_state": coordinator.zone_last_state,
        "zone_manual_lock_until": coordinator.zone_manual_lock_until,

        # Panic
        "panic_threshold": coordinator.panic_threshold,
        "panic_delay": coordinator.panic_delay,
        "last_panic_ts": coordinator.last_panic_ts,
        "panic_cooldown_active": panic_cooldown_active,

        # Master switch
        "master_off_since": coordinator.master_off_since,
    }
