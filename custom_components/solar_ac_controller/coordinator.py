from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .controller import SolarACController
from .const import (
    CONF_AC_POWER_SENSOR,
    CONF_AC_SWITCH,
    CONF_GRID_SENSOR,
    CONF_MANUAL_LOCK_SECONDS,
    CONF_PANIC_DELAY,
    CONF_PANIC_THRESHOLD,
    CONF_SHORT_CYCLE_OFF_SECONDS,
    CONF_SHORT_CYCLE_ON_SECONDS,
    CONF_SOLAR_SENSOR,
    CONF_SOLAR_THRESHOLD_OFF,
    CONF_SOLAR_THRESHOLD_ON,
    CONF_ZONES,
    CONF_ADD_CONFIDENCE,
    CONF_REMOVE_CONFIDENCE,
    CONF_INITIAL_LEARNED_POWER,
    CONF_ACTION_DELAY_SECONDS,
    STORAGE_KEY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

_PANIC_COOLDOWN_SECONDS = 120
_EMA_RESET_AFTER_OFF_SECONDS = 600

_DEFAULT_ADD_CONFIDENCE = 25
_DEFAULT_REMOVE_CONFIDENCE = 10


class SolarACCoordinator(DataUpdateCoordinator):
    """Main control loop for the Solar AC Controller.

    This coordinator also handles migration of stored learned_power from the
    legacy numeric-per-zone format to a per-mode structure:
      legacy: { "living_room": 1200, "bedroom": 900 }
      new:    { "living_room": {"default":1200, "heat":1200, "cool":1200}, ... }

    The coordinator exposes helper methods `get_learned_power` and
    `set_learned_power` so other modules (controller, sensors) can remain
    agnostic to the underlying storage format.
    """

    def __init__(self, hass: HomeAssistant, config_entry, store, stored: dict[str, Any], version: str):
        super().__init__(
            hass,
            logger=_LOGGER,
            name="Solar AC Controller",
            update_interval=timedelta(seconds=5),
        )

        self.hass = hass
        self.config_entry = config_entry
        self.config = dict(config_entry.data)
        self.store = store

        # Integration version (first-class field)
        self.version = version

        # Initial learned power (option or config)
        self.initial_learned_power = config_entry.options.get(
            CONF_INITIAL_LEARNED_POWER,
            config_entry.data.get(CONF_INITIAL_LEARNED_POWER, 1200),
        )

        # Load stored values and migrate if necessary
        raw_learned = stored.get("learned_power", {}) or {}
        raw_samples = stored.get("samples", 0) or 0

        # Ensure types
        self.learned_power: dict[str, dict[str, float]] = {}
        self.samples: int = int(raw_samples)

        # Perform migration if legacy format detected (values are numeric)
        migrated = False
        if isinstance(raw_learned, dict):
            for zone_name, val in raw_learned.items():
                # Legacy numeric value -> migrate to per-mode dict
                if isinstance(val, (int, float)):
                    migrated = True
                    v = float(val)
                    self.learned_power[zone_name] = {
                        "default": v,
                        "heat": v,
                        "cool": v,
                    }
                elif isinstance(val, dict):
                    # Already per-mode; normalize keys and ensure numeric values
                    normalized = {}
                    # Accept keys: default, heat, cool (case-insensitive)
                    for k, vv in val.items():
                        try:
                            normalized[k.lower()] = float(vv)
                        except Exception:
                            # Skip non-numeric entries
                            continue
                    # Ensure default exists
                    if "default" not in normalized:
                        # Prefer heat, then cool, then initial_learned_power
                        normalized["default"] = normalized.get("heat", normalized.get("cool", float(self.initial_learned_power)))
                    if "heat" not in normalized:
                        normalized["heat"] = normalized["default"]
                    if "cool" not in normalized:
                        normalized["cool"] = normalized["default"]
                    self.learned_power[zone_name] = normalized
                else:
                    # Unknown type: ignore and fallback to initial value
                    self.learned_power[zone_name] = {
                        "default": float(self.initial_learned_power),
                        "heat": float(self.initial_learned_power),
                        "cool": float(self.initial_learned_power),
                    }
        else:
            # No learned_power stored; initialize empty mapping
            self.learned_power = {}

        # If migration occurred, persist the new structure immediately
        if migrated:
            # Save migrated structure and samples back to storage
            try:
                # Build storage payload
                payload = {
                    "learned_power": dict(self.learned_power),
                    "samples": int(self.samples),
                }
                # Use store to persist
                hass.async_create_task(self.store.async_save(payload))
                _LOGGER.info("Migrated legacy learned_power to per-mode structure and saved storage")
            except Exception as exc:
                _LOGGER.exception("Failed to persist migrated learned_power: %s", exc)

        # Internal state
        self.last_action: str | None = None
        self.learning_active: bool = False
        self.learning_start_time: float | None = None
        self.ac_power_before: float | None = None
        self.learning_zone: str | None = None

        # EMA state
        self.ema_30s: float = 0.0
        self.ema_5m: float = 0.0

        # Short cycle memory
        self.zone_last_changed: dict[str, float] = {}
        self.zone_last_changed_type: dict[str, str] = {}

        # Manual override detection
        self.zone_last_state: dict[str, str | None] = {}
        self.zone_manual_lock_until: dict[str, float] = {}

        # Panic config
        self.panic_threshold: float = float(self.config.get(CONF_PANIC_THRESHOLD, 1500))
        self.panic_delay: int = int(self.config.get(CONF_PANIC_DELAY, 30))

        # Manual lock duration
        self.manual_lock_seconds: int = int(self.config.get(CONF_MANUAL_LOCK_SECONDS, 1200))

        # Short-cycle thresholds
        self.short_cycle_on_seconds: int = int(self.config.get(
            CONF_SHORT_CYCLE_ON_SECONDS, 1200
        ))
        self.short_cycle_off_seconds: int = int(self.config.get(
            CONF_SHORT_CYCLE_OFF_SECONDS, 1200
        ))

        # Delay between actions
        self.action_delay_seconds: int = int(self.config.get(CONF_ACTION_DELAY_SECONDS, 3))

        # Confidence thresholds
        self.add_confidence_threshold: float = float(self.config.get(
            CONF_ADD_CONFIDENCE, _DEFAULT_ADD_CONFIDENCE
        ))
        self.remove_confidence_threshold: float = float(self.config.get(
            CONF_REMOVE_CONFIDENCE, _DEFAULT_REMOVE_CONFIDENCE
        ))

        # Controller
        self.controller = SolarACController(hass, self, store)

        # Observability
        self.last_add_conf: float = 0.0
        self.last_remove_conf: float = 0.0
        self.confidence: float = 0.0
        self.last_action_start_ts: float | None = None
        self.last_action_duration: float | None = None

        # Background tasks
        self._panic_task: asyncio.Task | None = None
        self._master_shutdown_task: asyncio.Task | None = None

        # Master OFF tracking and panic cooldown
        self.master_off_since: float | None = None
        self.last_panic_ts: float | None = None

        # Exposed fields for sensors
        self.next_zone: str | None = None
        self.last_zone: str | None = None
        self.required_export: float | None = None
        self.export_margin: float | None = None

    # -------------------------------------------------------------------------
    # Helper accessors for learned_power (abstracts storage format)
    # -------------------------------------------------------------------------
    def get_learned_power(self, zone_name: str, mode: str | None = None) -> float:
        """Return the learned power for a zone.

        If the stored value is per-mode, prefer the requested mode, then 'default'.
        If nothing is stored, return initial_learned_power.
        """
        entry = self.learned_power.get(zone_name)
        if entry is None:
            return float(self.initial_learned_power)
        if isinstance(entry, dict):
            if mode and mode in entry:
                return float(entry.get(mode))
            if "default" in entry:
                return float(entry.get("default"))
            # Fallback to heat/cool if present
            if "heat" in entry:
                return float(entry.get("heat"))
            if "cool" in entry:
                return float(entry.get("cool"))
            # Last resort
            return float(self.initial_learned_power)
        # Backwards compatibility: numeric value
        try:
            return float(entry)
        except Exception:
            return float(self.initial_learned_power)

    def set_learned_power(self, zone_name: str, value: float, mode: str | None = None) -> None:
        """Set learned power for a zone.

        If the stored structure is per-mode (dict), update the requested mode and
        the 'default' key. If legacy numeric mapping is present, convert it to
        per-mode dict and then update.
        """
        if zone_name not in self.learned_power or not isinstance(self.learned_power.get(zone_name), dict):
            # Initialize per-mode dict
            base = float(self.learned_power.get(zone_name) if isinstance(self.learned_power.get(zone_name), (int, float)) else self.initial_learned_power)
            self.learned_power[zone_name] = {
                "default": base,
                "heat": base,
                "cool": base,
            }

        entry = self.learned_power[zone_name]
        # Update requested mode and default
        if mode:
            entry[mode] = float(value)
        entry["default"] = float(value)
        # Keep heat/cool present
        if "heat" not in entry:
            entry["heat"] = entry["default"]
        if "cool" not in entry:
            entry["cool"] = entry["default"]

    async def _persist_learned_values(self) -> None:
        """Persist learned_power and samples to storage (async)."""
        try:
            payload = {
                "learned_power": dict(self.learned_power),
                "samples": int(self.samples),
            }
            await self.store.async_save(payload)
        except Exception as exc:
            _LOGGER.exception("Error saving learned values: %s", exc)
            try:
                await self._log(f"[STORAGE_ERROR] {exc}")
            except Exception:
                _LOGGER.exception("Failed to write storage error to coordinator log")

    # -------------------------------------------------------------------------
    # Main update loop
    # -------------------------------------------------------------------------
    async def _async_update_data(self):
        """Main loop executed every 5 seconds."""

        # 1. Read sensors
        grid_state = self.hass.states.get(self.config.get(CONF_GRID_SENSOR))
        solar_state = self.hass.states.get(self.config.get(CONF_SOLAR_SENSOR))
        ac_state = self.hass.states.get(self.config.get(CONF_AC_POWER_SENSOR))

        if not grid_state or not solar_state or not ac_state:
            _LOGGER.debug("Missing sensor state, skipping cycle")
            return

        if ac_state.state in ("unknown", "unavailable"):
            self.last_action = "ac_sensor_unavailable"
            _LOGGER.debug("AC power sensor unavailable, skipping cycle")
            return

        try:
            grid_raw = float(grid_state.state)
            solar = float(solar_state.state)
            ac_power = float(ac_state.state)
        except (ValueError, TypeError):
            _LOGGER.debug("Non-numeric sensor value, skipping cycle")
            return

        _LOGGER.debug(
            "Cycle sensors: grid_raw=%s solar=%s ac_power=%s", grid_raw, solar, ac_power
        )

        # 2. EMA updates
        self._update_ema(grid_raw)

        # 3. Master switch handling
        await self._handle_master_switch(solar, ac_power)

        # 4. Master OFF guard
        if await self._handle_master_off_guard():
            return

        # 5. Determine zones and detect manual overrides
        active_zones = await self._update_zone_states_and_overrides()
        on_count = len(active_zones)

        # 6. Compute required export and confidences
        next_zone, last_zone = self._select_next_and_last_zone(active_zones)
        required_export = self._compute_required_export(next_zone)
        export = -self.ema_30s
        import_power = self.ema_5m

        # Store for sensors
        self.next_zone = next_zone
        self.last_zone = last_zone
        self.required_export = required_export
        self.export_margin = None if required_export is None else export - required_export

        self.last_add_conf = self._compute_add_conf(
            export=export,
            required_export=required_export,
            last_zone=last_zone,
        )
        self.last_remove_conf = self._compute_remove_conf(
            import_power=import_power,
            last_zone=last_zone,
        )

        # Unified confidence
        self.confidence = self.last_add_conf - self.last_remove_conf

        now_ts = dt_util.utcnow().timestamp()

        # 7. Learning timeout
        if self.learning_active and self.learning_start_time:
            if now_ts - self.learning_start_time >= 360:
                await self._log(f"[LEARNING_TIMEOUT] zone={self.learning_zone}")
                await self.controller.finish_learning()
                return

        # 8. Panic logic
        if self._should_panic(on_count):
            await self._schedule_panic(active_zones)
            return

        # 9. Panic cooldown
        if self._in_panic_cooldown(now_ts):
            self.last_action = "panic_cooldown"
            await self._log("[PANIC_COOLDOWN] skipping add/remove decisions")
            return

        # 10. ADD zone decision
        if next_zone and self._should_add_zone(next_zone, required_export):
            await self._attempt_add_zone(next_zone, ac_power, export, required_export)
            return

        # 11. REMOVE zone decision
        if last_zone and self._should_remove_zone(last_zone, import_power):
            await self._attempt_remove_zone(last_zone, import_power)
            return

        # 12. SYSTEM BALANCED
        self.last_action = "balanced"
        await self._log(
            f"[SYSTEM_BALANCED] ema30={round(self.ema_30s)} "
            f"ema5m={round(self.ema_5m)} zones={on_count} samples={self.samples}"
        )

    # -------------------------------------------------------------------------
    # EMA / metrics / guards
    # -------------------------------------------------------------------------
    def _update_ema(self, grid_raw: float) -> None:
        self.ema_30s = 0.25 * grid_raw + 0.75 * self.ema_30s
        self.ema_5m = 0.03 * grid_raw + 0.97 * self.ema_5m

    async def _handle_master_off_guard(self) -> bool:
        ac_switch = self.config.get(CONF_AC_SWITCH)
        if not ac_switch:
            return False

        switch_state_obj = self.hass.states.get(ac_switch)
        if not switch_state_obj:
            return False

        if switch_state_obj.state != "off":
            self.master_off_since = None
            return False

        # Master is OFF
        now_ts = dt_util.utcnow().timestamp()
        if self.master_off_since is None:
            self.master_off_since = now_ts

        # Cancel panic task
        if self._panic_task and not self._panic_task.done():
            self._panic_task.cancel()
            self._panic_task = None

        # Reset learning
        self.controller._reset_learning_state()

        # Reset EMA after long OFF
        if now_ts - self.master_off_since >= _EMA_RESET_AFTER_OFF_SECONDS:
            if self.ema_30s != 0.0 or self.ema_5m != 0.0:
                await self._log("[EMA_RESET_AFTER_MASTER_OFF] resetting EMA")
            self.ema_30s = 0.0
            self.ema_5m = 0.0

        self.last_action = "master_off"
        await self._log("[MASTER_OFF] skipping all zone calculations")
        return True

    def _in_panic_cooldown(self, now_ts: float) -> bool:
        if self.last_panic_ts is None:
            return False
        return (now_ts - self.last_panic_ts) < _PANIC_COOLDOWN_SECONDS

    # -------------------------------------------------------------------------
    # Zones, overrides, short-cycling
    # -------------------------------------------------------------------------
    async def _update_zone_states_and_overrides(self) -> list[str]:
        active_zones: list[str] = []

        for zone in self.config.get(CONF_ZONES, []):
            state_obj = self.hass.states.get(zone)
            if not state_obj:
                continue

            state = state_obj.state
            last_state = self.zone_last_state.get(zone)

            # Manual override detection
            if last_state is not None and last_state != state:
                if not (
                    self.last_action
                    and (
                        self.last_action.endswith(zone)
                        or self.last_action == "panic"
                    )
                ):
                    now_ts = dt_util.utcnow().timestamp()
                    self.zone_manual_lock_until[zone] = (
                        now_ts + self.manual_lock_seconds
                    )
                    await self._log(
                        f"[MANUAL_OVERRIDE] zone={zone} state={state} "
                        f"lock_until={int(self.zone_manual_lock_until[zone])}"
                    )

            self.zone_last_state[zone] = state

            # Treat heating, cooling and generic "on" as active
            if state in ("heat", "cool", "on"):
                active_zones.append(zone)

        return active_zones

    def _is_locked(self, zone_id: str) -> bool:
        until = self.zone_manual_lock_until.get(zone_id)
        return bool(until and dt_util.utcnow().timestamp() < until)

    def _select_next_and_last_zone(
        self, active_zones: list[str]
    ) -> tuple[str | None, str | None]:

        next_zone = next(
            (
                z
                for z in self.config.get(CONF_ZONES, [])
                if z not in active_zones and not self._is_locked(z)
            ),
            None,
        )

        last_zone = next(
            (z for z in reversed(active_zones) if not self._is_locked(z)),
            None,
        )

        return next_zone, last_zone

    def _compute_required_export(self, next_zone: str | None) -> float | None:
        if not next_zone:
            return None

        zone_name = next_zone.split(".")[-1]
        # We don't know the mode for a zone that is currently off; use 'default'
        lp = self.get_learned_power(zone_name, mode="default")
        safety_mult = 1.15 if self.samples >= 10 else 1.30
        return lp * safety_mult

    def _is_short_cycling(self, zone: str | None) -> bool:
        if not zone:
            return False
        last = self.zone_last_changed.get(zone)
        if not last:
            return False
        now = dt_util.utcnow().timestamp()
        last_type = self.zone_last_changed_type.get(zone)
        if last_type == "on":
            threshold = self.short_cycle_on_seconds
        elif last_type == "off":
            threshold = self.short_cycle_off_seconds
        else:
            threshold = self.short_cycle_off_seconds

        return (now - last) < threshold

    # -------------------------------------------------------------------------
    # Confidence calculations
    # -------------------------------------------------------------------------
    def _compute_add_conf(
        self,
        export: float,
        required_export: float | None,
        last_zone: str | None,
    ) -> float:

        if required_export is None:
            return 0.0

        export_margin = export - required_export

        base = min(40, max(0, export_margin / 25))
        sample_bonus = min(20, self.samples * 2)
        short_cycle_penalty = -30 if self._is_short_cycling(last_zone) else 0

        return base + 5 + sample_bonus + short_cycle_penalty

    def _compute_remove_conf(
        self,
        import_power: float,
        last_zone: str | None,
    ) -> float:

        base = min(60, max(0, (import_power - 200) / 8))
        heavy_import_bonus = 20 if import_power > 1500 else 0
        short_cycle_penalty = -40 if self._is_short_cycling(last_zone) else 0

        return base + 5 + heavy_import_bonus + short_cycle_penalty

    # -------------------------------------------------------------------------
    # Learning and panic
    # -------------------------------------------------------------------------
    def _should_panic(self, on_count: int) -> bool:
        return self.ema_30s > self.panic_threshold and on_count > 1

    async def _schedule_panic(self, active_zones: list[str]) -> None:
        if self.last_action != "panic":
            await self._log(
                f"[PANIC_SHED_TRIGGER] ema30={round(self.ema_30s)} "
                f"ema5m={round(self.ema_5m)} threshold={self.panic_threshold} "
                f"zones={active_zones}"
            )
            if not self._panic_task or self._panic_task.done():
                self._panic_task = self.hass.async_create_task(
                    self._panic_task_runner(active_zones)
                )

    async def _panic_shed(self, active_zones: list[str]):
        start = dt_util.utcnow().timestamp()
        for zone in active_zones[1:]:
            await self._call_entity_service(zone, False)
            await asyncio.sleep(self.action_delay_seconds)
        end = dt_util.utcnow().timestamp()
        self.last_action_start_ts = start
        self.last_action_duration = end - start

    async def _panic_task_runner(self, active_zones: list[str]):
        try:
            if self.panic_delay > 0:
                await asyncio.sleep(self.panic_delay)

            if self.ema_30s > self.panic_threshold:
                await self._panic_shed(active_zones)

                self.controller._reset_learning_state()

                now_ts = dt_util.utcnow().timestamp()
                self.last_panic_ts = now_ts

                await self._log(
                    f"[PANIC_SHED] ema30={round(self.ema_30s)} "
                    f"ema5m={round(self.ema_5m)} zones={active_zones}"
                )

                self.last_action = "panic"
        except Exception as e:
            _LOGGER.exception("Error in panic task: %s", e)
        finally:
            self._panic_task = None

    # -------------------------------------------------------------------------
    # Add / remove decisions
    # -------------------------------------------------------------------------
    def _should_add_zone(self, next_zone: str, required_export: float | None) -> bool:
        if self.learning_active:
            return False

        if self.ema_5m > -200:
            return False

        return self.confidence >= self.add_confidence_threshold

    def _should_remove_zone(self, last_zone: str, import_power: float) -> bool:
        return self.confidence <= -self.remove_confidence_threshold

    async def _attempt_add_zone(
        self,
        next_zone: str,
        ac_power_before: float,
        export: float,
        required_export: float,
    ) -> None:

        if self.last_action == f"add_{next_zone}":
            return

        await self._log(
            f"[ZONE_ADD_ATTEMPT] zone={next_zone} "
            f"add_conf={round(self.last_add_conf)} export={round(export)} "
            f"req_export={round(required_export)} samples={self.samples} "
            f"conf={round(self.confidence)} "
            f"thr_add={self.add_confidence_threshold} thr_rem={self.remove_confidence_threshold}"
        )

        await self._add_zone(next_zone, ac_power_before)
        self.last_action = f"add_{next_zone}"

    async def _attempt_remove_zone(
        self,
        last_zone: str,
        import_power: float,
    ) -> None:

        if self.last_action == f"remove_{last_zone}":
            return

        await self._log(
            f"[ZONE_REMOVE_ATTEMPT] zone={last_zone} "
            f"remove_conf={round(self.last_remove_conf)} import={round(import_power)} "
            f"short_cycling={self._is_short_cycling(last_zone)} "
            f"conf={round(self.confidence)} "
            f"thr_add={self.add_confidence_threshold} thr_rem={self.remove_confidence_threshold}"
        )
        await self._remove_zone(last_zone)
        self.last_action = f"remove_{last_zone}"

    async def _add_zone(self, zone: str, ac_power_before: float):
        """Start learning + turn on zone."""
        if self.learning_active:
            await self._log(
                f"[LEARNING_SKIPPED_ALREADY_ACTIVE] zone={zone} "
                f"current_zone={self.learning_zone}"
            )
            return

        # Mark learning before action, but actual power delta is validated later
        await self.controller.start_learning(zone, ac_power_before)

        start = dt_util.utcnow().timestamp()
        try:
            await self._call_entity_service(zone, True)
        finally:
            now_ts = dt_util.utcnow().timestamp()
            self.last_action_start_ts = start
            self.last_action_duration = now_ts - start
            self.zone_last_changed[zone] = now_ts
            self.zone_last_changed_type[zone] = "on"

        await asyncio.sleep(self.action_delay_seconds)

        await self._log(
            f"[LEARNING_START] zone={zone} ac_before={round(ac_power_before)} "
            f"samples={self.samples}"
        )

    async def _remove_zone(self, zone: str):
        """Turn off zone and update short-cycle memory."""
        start = dt_util.utcnow().timestamp()
        try:
            await self._call_entity_service(zone, False)
        finally:
            now_ts = dt_util.utcnow().timestamp()
            self.last_action_start_ts = start
            self.last_action_duration = now_ts - start
            self.zone_last_changed[zone] = now_ts
            self.zone_last_changed_type[zone] = "off"

        await asyncio.sleep(self.action_delay_seconds)

        await self._log(
            f"[ZONE_REMOVE_SUCCESS] zone={zone} import_after={round(self.ema_5m)}"
        )

    async def _call_entity_service(self, entity_id: str, turn_on: bool):
        """Call an appropriate turn_on/turn_off service for the entity's domain, with climate fallback."""
        domain = entity_id.split(".")[0]
        service = "turn_on" if turn_on else "turn_off"

        try:
            await self.hass.services.async_call(
                domain,
                service,
                {"entity_id": entity_id},
                blocking=True,
            )
            return
        except Exception as e:
            _LOGGER.debug(
                "Primary service %s.%s failed for %s: %s",
                domain,
                service,
                entity_id,
                e,
            )

        try:
            await self.hass.services.async_call(
                "climate",
                service,
                {"entity_id": entity_id},
                blocking=True,
            )
            _LOGGER.warning(
                "Primary service %s.%s failed for %s — used climate.%s as fallback",
                domain,
                service,
                entity_id,
                service,
            )
            return
        except Exception as e:
            _LOGGER.exception(
                "Fallback climate.%s failed for %s: %s", service, entity_id, e
            )

    # -------------------------------------------------------------------------
    # Master switch control
    # -------------------------------------------------------------------------
    async def _handle_master_switch(self, solar: float, ac_power: float):
        """Master relay control based on solar availability and compressor safety."""
        ac_switch = self.config.get(CONF_AC_SWITCH)
        if not ac_switch:
            return

        on_threshold = self.config.get(CONF_SOLAR_THRESHOLD_ON, 1200)
        off_threshold = self.config.get(CONF_SOLAR_THRESHOLD_OFF, 800)

        switch_state_obj = self.hass.states.get(ac_switch)
        if not switch_state_obj:
            return

        switch_state = switch_state_obj.state

        # Turn ON when solar is consistently above threshold
        if solar > on_threshold and switch_state == "off":
            await self._log(
                f"[MASTER_POWER_ON] solar={round(solar)}W ac_state={switch_state}"
            )
            await self.hass.services.async_call(
                "switch",
                "turn_on",
                {"entity_id": ac_switch},
                blocking=True,
            )
            # Clear manual locks when restoring master power
            if self.zone_manual_lock_until:
                self.zone_manual_lock_until.clear()
                await self._log(
                    "[MASTER_POWER_ON] cleared manual locks after OFF period"
                )

        # Turn OFF when solar is low and AC is idle (delayed for compressor safety)
        if solar < off_threshold and switch_state == "on":
            await self._log(
                f"[MASTER_SHUTDOWN] solar={round(solar)}W ac_power={round(ac_power)}W "
                "scheduling delayed shutdown for compressor safety"
            )
            if not self._master_shutdown_task or self._master_shutdown_task.done():
                self._master_shutdown_task = self.hass.async_create_task(
                    self._delayed_master_shutdown(ac_switch)
                )

    async def _delayed_master_shutdown(self, ac_switch: str):
        try:
            await asyncio.sleep(600)

            ac_state = self.hass.states.get(self.config.get(CONF_AC_POWER_SENSOR))
            try:
                ac_now = float(ac_state.state) if ac_state else 0.0
            except (ValueError, TypeError):
                ac_now = 0.0

            if ac_now < 25:
                await self.hass.services.async_call(
                    "switch",
                    "turn_off",
                    {"entity_id": ac_switch},
                    blocking=True,
                )
                await self._log(f"[MASTER_POWER_OFF] ac={round(ac_now)}W")
            else:
                await self._log(f"[MASTER_SHUTDOWN_BLOCKED] ac={round(ac_now)}W")
        except Exception as e:
            _LOGGER.exception("Error in delayed master shutdown: %s", e)
        finally:
            self._master_shutdown_task = None

    # -------------------------------------------------------------------------
    # Logging helper
    # -------------------------------------------------------------------------
    async def _log(self, message: str):
        """Log to HA logbook with a consistent taxonomy."""
        _LOGGER.info(message)
        try:
            await self.hass.services.async_call(
                "logbook",
                "log",
                {
                    "name": "Solar AC",
                    "entity_id": "sensor.solar_ac_controller_debug",
                    "message": message,
                },
                blocking=False,
            )
        except Exception:
            _LOGGER.exception("Failed to write to logbook: %s", message)
