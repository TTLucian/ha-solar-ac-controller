from __future__ import annotations

from typing import Any, Dict, List
from datetime import datetime

from homeassistant.util import dt as dt_util


def _safe_float(val: Any, default: float | None = None) -> float | None:
    try:
        return float(val)
    except Exception:
        return default


def build_diagnostics(coordinator: Any) -> Dict[str, Any]:
    """
    Build diagnostics payload for Solar AC Controller.

    Returns a dict containing only the fields listed in the diagnostics spec,
    with all ISO and epoch timestamps removed.
    Defensive: uses getattr and safe conversions so it never raises.
    """
    # Basic metadata
    version = getattr(coordinator, "version", None)
    try:
        version = str(version) if version is not None else None
    except Exception:
        version = None

    # Config snapshot (shallow copy of config dict)
    config = dict(getattr(coordinator, "config", {}) or {})

    # Learned / learning state
    samples = int(getattr(coordinator, "samples", 0) or 0)
    learned_power = dict(getattr(coordinator, "learned_power", {}) or {})
    learning_active = bool(getattr(coordinator, "learning_active", False))
    learning_zone = getattr(coordinator, "learning_zone", None)
    # learning_start_time removed per request
    ac_power_before = _safe_float(getattr(coordinator, "ac_power_before", None), None)

    # EMA metrics
    ema_30s = _safe_float(getattr(coordinator, "ema_30s", None), 0.0)
    ema_5m = _safe_float(getattr(coordinator, "ema_5m", None), 0.0)

    # Decision engine / action state
    last_action = getattr(coordinator, "last_action", None)
    next_zone = getattr(coordinator, "next_zone", None)
    last_zone = getattr(coordinator, "last_zone", None)
    required_export = _safe_float(getattr(coordinator, "required_export", None), None)
    export_margin = _safe_float(getattr(coordinator, "export_margin", None), None)

    # Zones and modes
    zones_config: List[str] = list(config.get("zones", []) or [])
    active_zones: List[str] = []
    zone_modes: Dict[str, str] = {}
    zone_last_changed = dict(getattr(coordinator, "zone_last_changed", {}) or {})
    zone_last_state = dict(getattr(coordinator, "zone_last_state", {}) or {})
    zone_manual_lock_until = dict(getattr(coordinator, "zone_manual_lock_until", {}) or {})

    for z in zones_config:
        st_obj = getattr(coordinator, "hass", None).states.get(z) if getattr(coordinator, "hass", None) else None
        state = None
        if st_obj:
            state = getattr(st_obj, "state", None)
        else:
            # fallback to coordinator-tracked state
            state = zone_last_state.get(z)
        if state in ("heat", "cool", "on"):
            active_zones.append(z)
        # mode detection: prefer hvac_mode/hvac_action attribute if available
        mode = None
        if st_obj:
            attrs = getattr(st_obj, "attributes", {}) or {}
            hvac_mode = attrs.get("hvac_mode") or attrs.get("hvac_action")
            if isinstance(hvac_mode, str):
                if "heat" in hvac_mode:
                    mode = "heat"
                elif "cool" in hvac_mode:
                    mode = "cool"
        if mode is None:
            if state == "heat":
                mode = "heat"
            elif state == "cool":
                mode = "cool"
            else:
                mode = "default"
        zone_modes[z] = mode

    # Panic / safety
    panic_threshold = _safe_float(getattr(coordinator, "panic_threshold", None), None)
    panic_delay = int(getattr(coordinator, "panic_delay", 0) or 0)
    # last_panic_ts removed per request
    panic_cooldown_active = False
    try:
        last_panic_ts = getattr(coordinator, "last_panic_ts", None)
        if last_panic_ts is not None:
            cooldown = getattr(coordinator, "panic_cooldown_seconds", None) or getattr(coordinator, "_PANIC_COOLDOWN_SECONDS", 120)
            panic_cooldown_active = (float(last_panic_ts) is not None) and bool(cooldown)
    except Exception:
        panic_cooldown_active = False

    # Master off tracking: remove master_off_since per request
    # Build payload without any ISO/epoch timestamps
    payload = {
        "version": version,
        "config": config,
        "samples": samples,
        "learned_power": learned_power,
        "learning_active": learning_active,
        "learning_zone": learning_zone,
        "ac_power_before": ac_power_before,
        "ema_30s": ema_30s,
        "ema_5m": ema_5m,
        "last_action": last_action,
        "next_zone": next_zone,
        "last_zone": last_zone,
        "required_export": required_export,
        "export_margin": export_margin,
        "active_zones": active_zones,
        "zone_modes": zone_modes,
        "zone_last_changed": zone_last_changed,
        "zone_last_state": zone_last_state,
        "zone_manual_lock_until": zone_manual_lock_until,
        "panic_threshold": panic_threshold,
        "panic_delay": panic_delay,
        "panic_cooldown_active": panic_cooldown_active,
    }

    return payload
