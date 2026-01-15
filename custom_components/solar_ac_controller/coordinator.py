# custom_components/solar_ac_controller/coordinator.py
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

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
    DEFAULT_INITIAL_LEARNED_POWER,
    DEFAULT_SOLAR_THRESHOLD_ON,
    DEFAULT_SOLAR_THRESHOLD_OFF,
    DEFAULT_PANIC_THRESHOLD,
    DEFAULT_PANIC_DELAY,
    DEFAULT_MANUAL_LOCK_SECONDS,
    DEFAULT_SHORT_CYCLE_ON_SECONDS,
    DEFAULT_SHORT_CYCLE_OFF_SECONDS,
    DEFAULT_ACTION_DELAY_SECONDS,
    DEFAULT_ADD_CONFIDENCE,
    DEFAULT_REMOVE_CONFIDENCE,
)

if TYPE_CHECKING:
    from .controller import SolarACController

_LOGGER = logging.getLogger(__name__)

_PANIC_COOLDOWN_SECONDS = 120
_EMA_RESET_AFTER_OFF_SECONDS = 600


class SolarACCoordinator(DataUpdateCoordinator):
    """Main control loop for the Solar AC Controller."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry,
        store,
        stored: dict[str, Any] | None,
        version: str,
    ):
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
        self.version = version

        # Initial learned power
        self.initial_learned_power = config_entry.options.get(
            CONF_INITIAL_LEARNED_POWER,
            config_entry.data.get(CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER),
        )

        # Stored migration
        stored = stored or {}
        raw_learned = stored.get("learned_power", {}) or {}
        raw_samples = stored.get("samples", 0) or 0

        self.learned_power: dict[str, dict[str, float]] = {}
        self.samples: int = int(raw_samples)

        migrated = False
        if isinstance(raw_learned, dict):
            for zone_name, val in raw_learned.items():
                if isinstance(val, (int, float)):
                    migrated = True
                    v = float(val)
                    self.learned_power[zone_name] = {"default": v, "heat": v, "cool": v}
                elif isinstance(val, dict):
                    normalized: dict[str, float] = {}
                    for k, vv in val.items():
                        try:
                            normalized[k.lower()] = float(vv)
                        except Exception:
                            continue
                    if "default" not in normalized:
                        normalized["default"] = normalized.get(
                            "heat", normalized.get("cool", float(self.initial_learned_power))
                        )
                    if "heat" not in normalized:
                        normalized["heat"] = normalized["default"]
                    if "cool" not in normalized:
                        normalized["cool"] = normalized["default"]
                    self.learned_power[zone_name] = normalized
                else:
                    self.learned_power[zone_name] = {
                        "default": float(self.initial_learned_power),
                        "heat": float(self.initial_learned_power),
                        "cool": float(self.initial_learned_power),
                    }
        else:
            self.learned_power = {}

        if migrated:
            try:
                payload = {"learned_power": dict(self.learned_power), "samples": int(self.samples)}

                async def _save_payload():
                    try:
                        await self.store.async_save(payload)
                        _LOGGER.info("Migrated legacy learned_power to per-mode structure and saved storage")
                    except Exception as exc:
                        _LOGGER.exception("Failed to persist migrated learned_power: %s", exc)

                hass.async_create_task(_save_payload())
            except Exception as exc:
                _LOGGER.exception("Failed to schedule persist of migrated learned_power: %s", exc)

        # Internal state
        self.last_action: str | None = None
        self.learning_active: bool = False
        self.learning_start_time: float | None = None
        self.ac_power_before: float | None = None
        self.learning_zone: str | None = None

        self.ema_30s: float = 0.0
        self.ema_5m: float = 0.0

        self.zone_last_changed: dict[str, float] = {}
        self.zone_last_changed_type: dict[str, str] = {}
        self.zone_last_state: dict[str, str | None] = {}
        self.zone_manual_lock_until: dict[str, float] = {}

        # Use centralized defaults from const.py
        self.panic_threshold: float = float(self.config.get(CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD))
        self.panic_delay: int = int(self.config.get(CONF_PANIC_DELAY, DEFAULT_PANIC_DELAY))
        self.manual_lock_seconds: int = int(self.config.get(CONF_MANUAL_LOCK_SECONDS, DEFAULT_MANUAL_LOCK_SECONDS))
        self.short_cycle_on_seconds: int = int(self.config.get(CONF_SHORT_CYCLE_ON_SECONDS, DEFAULT_SHORT_CYCLE_ON_SECONDS))
        self.short_cycle_off_seconds: int = int(self.config.get(CONF_SHORT_CYCLE_OFF_SECONDS, DEFAULT_SHORT_CYCLE_OFF_SECONDS))
        self.action_delay_seconds: int = int(self.config.get(CONF_ACTION_DELAY_SECONDS, DEFAULT_ACTION_DELAY_SECONDS))

        self.add_confidence_threshold: float = float(
            self.config.get(CONF_ADD_CONFIDENCE, DEFAULT_ADD_CONFIDENCE)
        )
        self.remove_confidence_threshold: float = float(
            self.config.get(CONF_REMOVE_CONFIDENCE, DEFAULT_REMOVE_CONFIDENCE)
        )

        from .controller import SolarACController
        self.controller: "SolarACController" = SolarACController(hass, self, store)

        self.last_add_conf: float = 0.0
        self.last_remove_conf: float = 0.0
        self.confidence: float = 0.0
        self.last_action_start_ts: float | None = None
        self.last_action_duration: float | None = None

        self._panic_task: asyncio.Task | None = None

        self.last_panic_ts: float | None = None

        self.next_zone: str | None = None
        self.last_zone: str | None = None
        self.required_export: float | None = None
        self.export_margin: float | None = None

        # Track when master was turned off for EMA reset
        self.master_off_since: float | None = None

    # -------------------------------------------------------------------------
    # Helper accessors for learned_power (abstracts storage format)
    # -------------------------------------------------------------------------
    def get_learned_power(self, zone_name: str, mode: str | None = None) -> float:
        entry = self.learned_power.get(zone_name)
        if entry is None:
            return float(self.initial_learned_power)
        if isinstance(entry, dict):
            if mode and mode in entry:
                return float(entry.get(mode))
            if "default" in entry:
                return float(entry.get("default"))
            if "heat" in entry:
                return float(entry.get("heat"))
            if "cool" in entry:
                return float(entry.get("cool"))
            return float(self.initial_learned_power)
        try:
            return float(entry)
        except Exception:
            return float(self.initial_learned_power)

    def set_learned_power(self, zone_name: str, value: float, mode: str | None = None) -> None:
        if zone_name not in self.learned_power or not isinstance(self.learned_power.get(zone_name), dict):
            base = float(
                self.learned_power.get(zone_name)
                if isinstance(self.learned_power.get(zone_name), (int, float))
                else self.initial_learned_power
            )
            self.learned_power[zone_name] = {
                "default": base,
                "heat": base,
                "cool": base,
            }

        entry = self.learned_power[zone_name]
        if mode:
            entry[mode] = float(value)
        entry["default"] = float(value)
        if "heat" not in entry:
            entry["heat"] = entry["default"]
        if "cool" not in entry:
            entry["cool"] = entry["default"]

    async def _persist_learned_values(self) -> None:
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
    # Minimal async logging hook used by coordinator and controller
    # -------------------------------------------------------------------------
    async def _log(self, message: str) -> None:
        """Async logging hook used by coordinator and controller."""
        try:
            # Keep this simple and non-blocking; expand if persistent logs are desired
            _LOGGER.info(message)
        except Exception:
            _LOGGER.debug("Failed to write coordinator log message: %s", message)

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

        # 2. Master switch auto-control (based ONLY on solar production)
        await self._handle_master_switch(solar)

        # 3. If master exists and is OFF -> perform full freeze cleanup then return
        ac_switch = self.config.get(CONF_AC_SWITCH)
        if ac_switch:
            switch_state_obj = self.hass.states.get(ac_switch)
            if switch_state_obj and switch_state_obj.state == "off":
                # Ensure any running tasks are cancelled and learning reset
                await self._perform_freeze_cleanup()
                self.last_action = "master_off"
                await self._log("[MASTER_OFF] master switch is off, freezing all calculations")
                return

        # 4. EMA updates
        self._update_ema(grid_raw)

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

    async def _perform_freeze_cleanup(self) -> None:
        """Cancel tasks and reset learning state when master is off."""
        # Cancel panic task
        if self._panic_task and not self._panic_task.done():
            try:
                self._panic_task.cancel()
            except Exception:
                _LOGGER.debug("Failed to cancel panic task")
            self._panic_task = None

        # Reset controller learning state (safe)
        try:
            if getattr(self, "controller", None) is not None:
                await self.controller._reset_learning_state_async()
        except Exception:
            _LOGGER.debug("Controller reset learning method failed or controller not set")

        # Track master_off_since for EMA reset
        now_ts = dt_util.utcnow().timestamp()
        if self.master_off_since is None:
            self.master_off_since = now_ts

        # Reset EMA after long OFF
        if now_ts - self.master_off_since >= _EMA_RESET_AFTER_OFF_SECONDS:
            if self.ema_30s != 0.0 or self.ema_5m != 0.0:
                await self._log("[EMA_RESET_AFTER_MASTER_OFF] resetting EMA")
            self.ema_30s = 0.0
            self.ema_5m = 0.0

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
        """Compute required export for the next zone.

        The required export is the learned power estimate for the zone.
        No safety multiplier is applied.
        """
        if not next_zone:
            return None

        zone_name = next_zone.split(".")[-1]
        lp = self.get_learned_power(zone_name, mode="default")
        return float(lp)

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

            # If master turned off during delay, abort
            ac_switch = self.config.get(CONF_AC_SWITCH)
            if ac_switch:
                st = self.hass.states.get(ac_switch)
                if st and st.state == "off":
                    await self._log("[PANIC_ABORTED] master switch turned off during panic delay")
                    return

            if self.ema_30s > self.panic_threshold:
                await self._panic_shed(active_zones)

                # Reset learning state via controller if available
                try:
                    if getattr(self, "controller", None) is not None:
                        await self.controller._reset_learning_state_async()
                except Exception:
                    _LOGGER.debug("Controller reset learning method failed or controller not set")

                now_ts = dt_util.utcnow().timestamp()
                self.last_panic_ts = now_ts

                await self._log(
                    f"[PANIC_SHED] ema30={round(self.ema_30s)} "
                    f"ema5m={round(self.ema_5m)} zones={active_zones}"
                )

                self.last_action = "panic"
        except asyncio.CancelledError:
            _LOGGER.debug("Panic task cancelled")
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

        return self.last_add_conf >= self.add_confidence_threshold

    def _should_remove_zone(self, last_zone: str, import_power: float) -> bool:
        return self.last_remove_conf >= self.remove_confidence_threshold

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
    async def _handle_master_switch(self, solar: float):
        """Master relay control based solely on solar production thresholds."""
        ac_switch = self.config.get(CONF_AC_SWITCH)
        if not ac_switch:
            return

        try:
            on_threshold = float(self.config.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON))
        except (TypeError, ValueError):
            on_threshold = DEFAULT_SOLAR_THRESHOLD_ON

        try:
            off_threshold = float(self.config.get(CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF))
        except (TypeError, ValueError):
            off_threshold = DEFAULT_SOLAR_THRESHOLD_OFF

        switch_state_obj = self.hass.states.get(ac_switch)
        if not switch_state_obj:
            return

        switch_state = switch_state_obj.state

        # Turn ON when solar is above or equal to ON threshold
        if solar >= on_threshold and switch_state == "off":
            await self._log(
                f"[MASTER_ON] solar={round(solar)} threshold_on={on_threshold}"
            )
            await self.hass.services.async_call(
                "switch",
                "turn_on",
                {"entity_id": ac_switch},
                blocking=True,
            )
            self.last_action = "master_on"
            # reset master_off_since when turned on
            self.master_off_since = None
            return

        # Turn OFF when solar is below or equal to OFF threshold
        if solar <= off_threshold and switch_state == "on":
            await self._log(
                f"[MASTER_OFF_TRIGGER] solar={round(solar)} threshold_off={off_threshold}"
            )
            await self.hass.services.async_call(
                "switch",
                "turn_off",
                {"entity_id": ac_switch},
                blocking=True,
            )
            self.last_action = "master_off"
            # mark master_off_since for EMA reset logic
            self.master_off_since = dt_util.utcnow().timestamp()
            return
