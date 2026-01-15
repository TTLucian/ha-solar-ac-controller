# custom_components/solar_ac_controller/helpers.py
from __future__ import annotations

from typing import Any, Dict, List
from datetime import datetime

from homeassistant.util import dt as dt_util


def _safe_float(val: Any, default: float | None = None) -> float | None:
    """Safely convert a value to float, or return default if conversion fails."""
    try:
        return float(val)
    except Exception:
        return default


def _human_delta(ts: float | None) -> str | None:
    """Return a human-readable time delta string for a timestamp."""
    if not ts:
        return None
    try:
        now = dt_util.utcnow().timestamp()
        diff = int(now - float(ts))
        if diff < 0:
            return "in the future"
        if diff < 5:
            return "just now"
        if diff < 60:
            return f"{diff}s ago"
        minutes = diff // 60
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        return f"{days}d ago"
    except Exception:
        return None


def build_diagnostics(coordinator: Any) -> Dict[str, Any]:
    """
    Build diagnostics payload for Solar AC Controller.

    The diagnostics payload explicitly documents that required_export is
    the learned power estimate (no safety multiplier).
    """
    """
    Build diagnostics payload for Solar AC Controller.

    The diagnostics payload explicitly documents that required_export is
    the learned power estimate (no safety multiplier).
    """
    version = getattr(coordinator, "version", None)
    try:
        version = str(version) if version is not None else None
    except Exception:
        version = None

    config = dict(getattr(coordinator, "config", {}) or {})

    samples = int(getattr(coordinator, "samples", 0) or 0)
    learned_power = dict(getattr(coordinator, "learned_power", {}) or {})
    learning_active = bool(getattr(coordinator, "learning_active", False))
    learning_zone = getattr(coordinator, "learning_zone", None)
    learning_start_time_ts = getattr(coordinator, "learning_start_time", None)
    learning_started = _human_delta(learning_start_time_ts)
    ac_power_before = _safe_float(getattr(coordinator, "ac_power_before", None), None)

    ema_30s = _safe_float(getattr(coordinator, "ema_30s", None), 0.0)
    ema_5m = _safe_float(getattr(coordinator, "ema_5m", None), 0.0)

    last_action = getattr(coordinator, "last_action", None)
    next_zone = getattr(coordinator, "next_zone", None)
    last_zone = getattr(coordinator, "last_zone", None)
    required_export = _safe_float(getattr(coordinator, "required_export", None), None)
    export_margin = _safe_float(getattr(coordinator, "export_margin", None), None)

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
            state = zone_last_state.get(z)
        if state in ("heat", "cool", "on"):
            active_zones.append(z)

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

    panic_threshold = _safe_float(getattr(coordinator, "panic_threshold", None), None)
    panic_delay = int(getattr(coordinator, "panic_delay", 0) or 0)
    last_panic_ts = getattr(coordinator, "last_panic_ts", None)
    last_panic = _human_delta(last_panic_ts)
    panic_cooldown_active = False
    try:
        if last_panic_ts is not None:
            cooldown = getattr(coordinator, "panic_cooldown_seconds", None) or getattr(coordinator, "_PANIC_COOLDOWN_SECONDS", 120)
            now = dt_util.utcnow().timestamp()
            panic_cooldown_active = (now - float(last_panic_ts)) < float(cooldown)
    except Exception:
        panic_cooldown_active = False

    master_off_since_ts = getattr(coordinator, "master_off_since", None)
    master_off = _human_delta(master_off_since_ts)

    payload = {
        "version": version,
        "config": config,
        "samples": samples,
        "learned_power": learned_power,
        "learning_active": learning_active,
        "learning_zone": learning_zone,
        "learning_started": learning_started,
        "ac_power_before": ac_power_before,
        "ema_30s": ema_30s,
        "ema_5m": ema_5m,
        "last_action": last_action,
        "next_zone": next_zone,
        "last_zone": last_zone,
        "required_export": required_export,
        "export_margin": export_margin,
        "required_export_source": "learned_power",
        "note": "Safety multiplier removed; required_export equals learned power estimate.",
        "active_zones": active_zones,
        "zone_modes": zone_modes,
        "zone_last_changed": zone_last_changed,
        "zone_last_state": zone_last_state,
        "zone_manual_lock_until": zone_manual_lock_until,
        "panic_threshold": panic_threshold,
        "panic_delay": panic_delay,
        "last_panic": last_panic,
        "panic_cooldown_active": panic_cooldown_active,
        "master_off": master_off,
    }

    return payload
