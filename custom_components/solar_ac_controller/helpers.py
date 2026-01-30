# custom_components/solar_ac_controller/helpers.py
from __future__ import annotations

from typing import Any, Dict, List

from homeassistant.util import dt as dt_util


def short_entity_name(entity_id: str) -> str:
    """Extract the short name from an entity ID (e.g., 'climate.zone1' -> 'zone1')."""
    return entity_id.split(".")[-1]


class EmaTracker:
    """Tracks EMA values for power metrics."""

    __slots__ = ("alpha_30s", "alpha_5m", "ema_30s", "ema_5m")

    def __init__(self, alpha_30s: float, alpha_5m: float) -> None:
        self.alpha_30s = alpha_30s
        self.alpha_5m = alpha_5m
        self.ema_30s = 0.0
        self.ema_5m = 0.0

    def update(self, value: float) -> tuple[float, float]:
        """Update EMA values with new measurement."""
        self.ema_30s = self.alpha_30s * value + (1 - self.alpha_30s) * self.ema_30s
        self.ema_5m = self.alpha_5m * value + (1 - self.alpha_5m) * self.ema_5m
        return self.ema_30s, self.ema_5m

    def reset(self) -> None:
        """Reset EMA values to zero."""
        self.ema_30s = 0.0
        self.ema_5m = 0.0

    @property
    def current_30s(self) -> float:
        return self.ema_30s

    @property
    def current_5m(self) -> float:
        return self.ema_5m


class MasterSwitchController:
    """Handles master AC switch auto-control logic."""

    __slots__ = ("coordinator",)

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator

    async def handle_master_switch(self, solar: float, cycle_start: Any) -> None:
        """Master relay control with sticky manual lock until natural solar cycle aligns."""
        ac_switch = self.coordinator.config_manager.get("ac_switch")
        if not ac_switch:
            return

        try:
            on_threshold = self.coordinator.config_manager.get_float(
                "solar_threshold_on", 1200.0
            )
        except (TypeError, ValueError):
            on_threshold = 1200.0

        try:
            off_threshold = self.coordinator.config_manager.get_float(
                "solar_threshold_off", 800.0
            )
        except (TypeError, ValueError):
            off_threshold = 800.0

        switch_state_obj = self.coordinator.hass.states.get(ac_switch)
        if not switch_state_obj:
            return

        switch_state = switch_state_obj.state

        # Detect manual changes (state changed without recent coordinator action)
        if (
            self.coordinator.master_last_state is not None
            and switch_state != self.coordinator.master_last_state
        ):
            now = dt_util.utcnow().timestamp()
            # If no recent coordinator action (within 10s), it's a manual change
            if (
                self.coordinator.master_last_action_time is None
                or (now - self.coordinator.master_last_action_time) > 10
            ):
                self.coordinator.master_manual_lock_state = switch_state
                await self.coordinator._log(
                    f"[MASTER_MANUAL_LOCK] detected manual change to {switch_state}, locking until natural cycle aligns"
                )

        # Check if lock should be released
        if self.coordinator.master_manual_lock_state is not None:
            # Release lock if locked ON and solar would naturally turn it ON
            if (
                self.coordinator.master_manual_lock_state == "on"
                and solar >= on_threshold
            ):
                await self.coordinator._log(
                    f"[MASTER_LOCK_RELEASE] solar={round(solar)} >= threshold_on={on_threshold}, resuming auto-control"
                )
                self.coordinator.master_manual_lock_state = None
            # Release lock if locked OFF and solar would naturally turn it OFF
            elif (
                self.coordinator.master_manual_lock_state == "off"
                and solar <= off_threshold
            ):
                await self.coordinator._log(
                    f"[MASTER_LOCK_RELEASE] solar={round(solar)} <= threshold_off={off_threshold}, resuming auto-control"
                )
                self.coordinator.master_manual_lock_state = None
            else:
                # Still locked, skip auto-control
                self.coordinator.master_last_state = switch_state
                return

        # Update last known state
        self.coordinator.master_last_state = switch_state

        # Normal auto-control (only when not locked)
        # Turn ON when solar is above or equal to ON threshold
        if solar >= on_threshold and switch_state == "off":
            await self.coordinator._log(
                f"[MASTER_ON] solar={round(solar)}W >= threshold_on={on_threshold}W, "
                f"turning AC master switch ON"
            )
            await self.coordinator.hass.services.async_call(
                "switch",
                "turn_on",
                {"entity_id": ac_switch},
                blocking=True,
            )
            self.coordinator.last_action = "master_on"
            self.coordinator.master_last_action_time = dt_util.utcnow().timestamp()
            # reset master_off_since when turned on
            self.coordinator.master_off_since = None
            return

        # Turn OFF when solar is below or equal to OFF threshold
        if solar <= off_threshold and switch_state == "on":
            await self.coordinator._log(
                f"[MASTER_OFF_TRIGGER] solar={round(solar)}W <= threshold_off={off_threshold}W, "
                f"turning AC master switch OFF"
            )
            await self.coordinator.hass.services.async_call(
                "switch",
                "turn_off",
                {"entity_id": ac_switch},
                blocking=True,
            )
            self.coordinator.last_action = "master_off"
            self.coordinator.master_last_action_time = dt_util.utcnow().timestamp()
            # mark master_off_since for EMA reset logic
            self.coordinator.master_off_since = dt_util.utcnow().timestamp()
            # Record cycle end since we're returning early
            self.coordinator.metrics.record_cycle_end(cycle_start, success=True)
            return


def _safe_float(val: Any, default: float | None = None) -> float | None:
    """Safely convert a value to float, or return default if conversion fails."""
    try:
        return float(val)
    except (ValueError, TypeError):
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
    except (ValueError, TypeError, AttributeError):
        return None


def _iso_ts(ts: float | None) -> str | None:
    """Return ISO8601 UTC string for a timestamp (seconds precision)."""
    if ts is None:
        return None
    try:
        dt = dt_util.utc_from_timestamp(float(ts))
        # Use seconds precision to avoid noisy fractions
        return dt.replace(microsecond=0).isoformat()
    except (ValueError, TypeError, OSError):
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
    except (ValueError, TypeError):
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
    ac_power_after = _safe_float(getattr(coordinator, "ac_power_after", None), None)

    ema_30s = _safe_float(getattr(coordinator, "ema_30s", None), 0.0)
    ema_5m = _safe_float(getattr(coordinator, "ema_5m", None), 0.0)

    # Removed: outside_temp and outside_band
    season_mode = getattr(coordinator, "season_mode", None)
    enable_temp_modulation = bool(getattr(coordinator, "enable_temp_modulation", False))

    last_action = getattr(coordinator, "last_action", None)
    next_zone = getattr(coordinator, "next_zone", None)
    last_zone = getattr(coordinator, "last_zone", None)
    required_export = _safe_float(getattr(coordinator, "required_export", None), None)
    export_margin = _safe_float(getattr(coordinator, "export_margin", None), None)
    # Source: use coordinator-provided source, fall back to inference
    req_src = getattr(coordinator, "required_export_source", None)
    if not isinstance(req_src, str) or not req_src:
        try:
            zm = getattr(coordinator, "zone_manual_power", {}) or {}
            req_src = (
                "manual_power" if next_zone and next_zone in zm else "learned_power"
            )
        except (AttributeError, TypeError, KeyError):
            req_src = "learned_power"

    zones_config: List[str] = list(config.get("zones", []) or [])
    active_zones: List[str] = []
    zone_modes: Dict[str, str] = {}
    zone_last_changed = dict(getattr(coordinator, "zone_last_changed", {}) or {})
    zone_last_state = dict(getattr(coordinator, "zone_last_state", {}) or {})
    zone_manual_lock_until = dict(
        getattr(coordinator, "zone_manual_lock_until", {}) or {}
    )

    # Master switch manual lock state
    master_last_state = getattr(coordinator, "master_last_state", None)
    master_manual_lock_state = getattr(coordinator, "master_manual_lock_state", None)

    for z in zones_config:
        try:
            hass = getattr(coordinator, "hass", None)
            st_obj = None
            if (
                hass is not None
                and hasattr(hass, "states")
                and hasattr(hass.states, "get")
            ):
                st_obj = hass.states.get(z)
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
        except (AttributeError, TypeError, KeyError, ValueError) as exc:
            zone_modes[z] = f"diagnostics_error: {exc}"

    panic_threshold = _safe_float(getattr(coordinator, "panic_threshold", None), None)
    panic_delay = int(getattr(coordinator, "panic_delay", 0) or 0)
    last_panic_ts = getattr(coordinator, "last_panic_ts", None)
    last_panic = _human_delta(last_panic_ts)
    panic_cooldown_active = (
        getattr(coordinator, "panic_manager", None)
        and coordinator.panic_manager.is_in_cooldown
    )

    master_off_since_raw = getattr(coordinator, "master_off_since", None)
    master_off = _human_delta(master_off_since_raw)

    # Last action timestamps/durations if available
    last_action_start_ts = getattr(coordinator, "last_action_start_ts", None)
    last_action_started = _human_delta(last_action_start_ts)
    last_action_duration = None
    try:
        dur = getattr(coordinator, "last_action_duration", None)
        last_action_duration = round(float(dur), 2) if dur is not None else None
    except (ValueError, TypeError):
        last_action_duration = None

    # Comfort temperature targets
    max_temp_winter = _safe_float(getattr(coordinator, "max_temp_winter", None), None)
    min_temp_summer = _safe_float(getattr(coordinator, "min_temp_summer", None), None)
    zone_current_temps = dict(getattr(coordinator, "zone_current_temps", {}) or {})
    # Sanitize zone temps to remove None values and round for readability
    zone_temps_rounded = {
        k: round(v, 1) if v is not None else None for k, v in zone_current_temps.items()
    }
    # Check if last_zone is at target (for diagnostics)
    last_zone_at_target = bool(
        getattr(coordinator, "_all_active_zones_at_target", lambda x: True)(last_zone)
    )

    # Add raw timestamps for automation/debugging
    payload = {
        "version": version,
        "config": config,
        "samples": samples,
        "learned_power": learned_power,
        "learning_active": learning_active,
        "learning_zone": learning_zone,
        "learning_started": learning_started,
        "ac_power_before": ac_power_before,
        "ac_power_after": ac_power_after,
        "ema_30s": ema_30s,
        "ema_5m": ema_5m,
        # Removed: outside_temp and outside_band
        "season_mode": season_mode,
        "enable_temp_modulation": enable_temp_modulation,
        "last_action": last_action,
        "last_action_started": last_action_started,
        "last_action_duration_s": last_action_duration,
        "next_zone": next_zone,
        "last_zone": last_zone,
        "required_export": required_export,
        "export_margin": export_margin,
        "required_export_source": req_src,
        "active_zones": active_zones,
        "zone_modes": zone_modes,
        "zone_last_changed": zone_last_changed,
        "zone_manual_lock_until": zone_manual_lock_until,
        "master_last_state": master_last_state,
        "master_manual_lock_state": master_manual_lock_state,
        "panic_threshold": panic_threshold,
        "panic_delay": panic_delay,
        "last_panic": last_panic,
        "panic_cooldown_active": panic_cooldown_active,
        "master_off": master_off,
        "max_temp_winter": max_temp_winter,
        "min_temp_summer": min_temp_summer,
        "zone_current_temps": zone_temps_rounded,
        "last_zone_at_target": last_zone_at_target,
    }

    # Extensibility: auto-discover simple coordinator attributes not already included
    known_keys = set(payload.keys())
    # Exclude coordinator attributes we've already processed with human formatting
    excluded_attrs = {
        "master_off_since",
        "last_panic_ts",
        "learning_start_time",
        "last_action_start_ts",
        "name",
    }
    for attr in dir(coordinator):
        if attr.startswith("_") or attr in known_keys or attr in excluded_attrs:
            continue
        try:
            val = getattr(coordinator, attr)
            if isinstance(val, (str, int, float, bool)):
                payload[attr] = val
        except (AttributeError, TypeError):
            continue

    # Privacy/Security: Remove any attribute that looks like a token/secret
    for k in list(payload.keys()):
        if "token" in k or "secret" in k or "password" in k:
            payload.pop(k)

    return payload
