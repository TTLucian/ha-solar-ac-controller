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
    version = getattr(coordinator, "version", None)
    try:
        version = str(version) if version is not None else None
    except Exception:
        version = None

    config = dict(getattr(coordinator, "config", {}) or {})

    samples = int(getattr(coordinator, "samples", 0) or 0)
    # Limit learned_power dict size for attribute payload (HA truncates large attributes)
    learned_power = dict(getattr(coordinator, "learned_power", {}) or {})
    if len(learned_power) > 20:
        learned_power = {k: learned_power[k] for k in list(learned_power)[:20]}
        learned_power["_truncated"] = f"{len(learned_power)}+ entries, truncated"

    learning_active = bool(getattr(coordinator, "learning_active", False))
    learning_zone = getattr(coordinator, "learning_zone", None)
    learning_start_time_ts = getattr(coordinator, "learning_start_time", None)
    learning_started = _human_delta(learning_start_time_ts)
    ac_power_before = _safe_float(getattr(coordinator, "ac_power_before", None), None)

    ema_30s = _safe_float(getattr(coordinator, "ema_30s", None), 0.0)
    ema_5m = _safe_float(getattr(coordinator, "ema_5m", None), 0.0)

    outside_temp = _safe_float(getattr(coordinator, "outside_temp", None), None)
    outside_temp_rolling_mean = _safe_float(getattr(coordinator, "outside_temp_rolling_mean", None), None)
    outside_band = getattr(coordinator, "outside_band", None)
    season_mode = getattr(coordinator, "season_mode", None)
    enable_auto_season = bool(getattr(coordinator, "enable_auto_season", False))
    enable_temp_modulation = bool(getattr(coordinator, "enable_temp_modulation", False))
    master_off_in_neutral = bool(getattr(coordinator, "master_off_in_neutral", False))

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
        try:
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
        except Exception as exc:
            zone_modes[z] = f"diagnostics_error: {exc}"

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

    # Comfort temperature targets
    max_temp_winter = _safe_float(getattr(coordinator, "max_temp_winter", None), None)
    min_temp_summer = _safe_float(getattr(coordinator, "min_temp_summer", None), None)
    zone_current_temps = dict(getattr(coordinator, "zone_current_temps", {}) or {})
    # Sanitize zone temps to remove None values and round for readability
    zone_temps_rounded = {k: round(v, 1) if v is not None else None for k, v in zone_current_temps.items()}
    all_zones_at_target = bool(getattr(coordinator, "_all_active_zones_at_target", lambda x: False)(active_zones))

    # Add raw timestamps for automation/debugging
    payload = {
        "version": version,
        "manifest_version": version,
        "config": config,
        "samples": samples,
        "learned_power": learned_power,
        "learning_active": learning_active,
        "learning_zone": learning_zone,
        "learning_started": learning_started,
        "learning_start_time_ts": learning_start_time_ts,
        "ac_power_before": ac_power_before,
        "ema_30s": ema_30s,
        "ema_5m": ema_5m,
        "outside_temp": outside_temp,
        "outside_temp_rolling_mean": outside_temp_rolling_mean,
        "outside_band": outside_band,
        "season_mode": season_mode,
        "enable_auto_season": enable_auto_season,
        "enable_temp_modulation": enable_temp_modulation,
        "master_off_in_neutral": master_off_in_neutral,
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
        "last_panic_ts": last_panic_ts,
        "panic_cooldown_active": panic_cooldown_active,
        "master_off": master_off,
        "master_off_since_ts": master_off_since_ts,
        "max_temp_winter": max_temp_winter,
        "min_temp_summer": min_temp_summer,
        "zone_current_temps": zone_temps_rounded,
        "all_zones_at_target": all_zones_at_target,
    }

    # Extensibility: auto-discover simple coordinator attributes not already included
    known_keys = set(payload.keys())
    # Exclude coordinator attributes we've already processed with human formatting
    excluded_attrs = {"master_off_since", "last_panic_ts", "learning_start_time"}
    for attr in dir(coordinator):
        if attr.startswith("_") or attr in known_keys or attr in excluded_attrs:
            continue
        try:
            val = getattr(coordinator, attr)
            if isinstance(val, (str, int, float, bool)):
                payload[attr] = val
        except Exception:
            continue

    # Privacy/Security: Remove any attribute that looks like a token/secret
    for k in list(payload.keys()):
        if "token" in k or "secret" in k or "password" in k:
            payload.pop(k)

    return payload
