from __future__ import annotations

from homeassistant.util import dt as dt_util

from .const import CONF_ZONES

_PANIC_COOLDOWN_SECONDS = 120


def build_diagnostics(coordinator):
    """Return a unified diagnostics structure for both sensor and HA diagnostics.

    Uses coordinator.version as the authoritative integration version and
    converts mappingproxy config to a plain dict for JSON safety.
    """
    now_ts = dt_util.utcnow().timestamp()

    # Zones list from config (JSON-safe)
    zones = coordinator.config.get(CONF_ZONES, []) or []

    # Active zones (heat/cool/on)
    active_zones = []
    zone_modes = {}
    for z in zones:
        st = coordinator.hass.states.get(z)
        mode = st.state if st else None
        zone_modes[z] = mode
        if mode in ("heat", "cool", "on"):
            active_zones.append(z)

    # Panic cooldown state
    panic_cooldown_active = False
    if coordinator.last_panic_ts:
        panic_cooldown_active = (now_ts - coordinator.last_panic_ts) < _PANIC_COOLDOWN_SECONDS

    # JSON-safe learned_power copy (normalize to dict)
    learned_power = dict(coordinator.learned_power) if coordinator.learned_power is not None else {}

    return {
        # Authoritative integration version (fallback to None if missing)
        "version": getattr(coordinator, "version", None),

        # Timestamp for diagnostics snapshot (ISO)
        "timestamp": dt_util.utcnow().isoformat(),

        # JSON-safe config
        "config": dict(coordinator.config),

        # Learning
        "samples": int(coordinator.samples or 0),
        "learned_power": learned_power,
        "learning_active": bool(coordinator.learning_active),
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
        "zone_modes": zone_modes,
        "zone_last_changed": coordinator.zone_last_changed,
        "zone_last_state": coordinator.zone_last_state,
        "zone_manual_lock_until": coordinator.zone_manual_lock_until,

        # Panic
        "panic_threshold": coordinator.panic_threshold,
        "panic_delay": coordinator.panic_delay,
        "last_panic_ts": int(coordinator.last_panic_ts) if coordinator.last_panic_ts else None,
        "panic_cooldown_active": panic_cooldown_active,

        # Master switch
        "master_off_since": int(coordinator.master_off_since) if coordinator.master_off_since else None,
    }
