from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

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
)

_LOGGER = logging.getLogger(__name__)

# Internal behavioral constants (no config/UI exposure for now)
_PANIC_COOLDOWN_SECONDS = 120          # No add/remove for 2 minutes after panic
_EMA_RESET_AFTER_OFF_SECONDS = 600     # Reset EMA after 10 minutes master OFF
_MAX_ZONES_DEFAULT = 3                 # Hard cap on concurrently active zones

# Confidence defaults (UI passes positive values; remove is treated as negative internally)
_DEFAULT_ADD_CONFIDENCE = 25
_DEFAULT_REMOVE_CONFIDENCE = 10


class SolarACCoordinator(DataUpdateCoordinator):
    """Main control loop for the Solar AC Controller."""

    def __init__(self, hass: HomeAssistant, config, store, stored):
        super().__init__(
            hass,
            logger=_LOGGER,
            name="Solar AC Controller",
            update_interval=timedelta(seconds=5),
        )

        self.hass = hass
        self.config = config
        self.store = store

        # Learned values from storage
        self.learned_power: dict[str, float] = stored.get("learned_power", {})
        self.samples: int = stored.get("samples", 0)

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
        self.panic_threshold: float = config.get(CONF_PANIC_THRESHOLD, 1500)
        self.panic_delay: int = config.get(CONF_PANIC_DELAY, 10)

        # Manual lock duration
        self.manual_lock_seconds: int = config.get(CONF_MANUAL_LOCK_SECONDS, 1200)

        # Short-cycle thresholds
        self.short_cycle_on_seconds: int = config.get(CONF_SHORT_CYCLE_ON_SECONDS, 1200)
        self.short_cycle_off_seconds: int = config.get(CONF_SHORT_CYCLE_OFF_SECONDS, 1200)

        # Delay between actions
        self.action_delay_seconds: int = config.get("action_delay_seconds", 3)

        # Confidence thresholds (ADD positive, REMOVE is magnitude for negative side)
        self.add_confidence_threshold: float = config.get(
            CONF_ADD_CONFIDENCE,
            _DEFAULT_ADD_CONFIDENCE,
        )
        self.remove_confidence_threshold: float = config.get(
            CONF_REMOVE_CONFIDENCE,
            _DEFAULT_REMOVE_CONFIDENCE,
        )

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
        self.required_export: float = 0.0
        self.export_margin: float = 0.0

    # -------------------------------------------------------------------------
    # Main update loop
    # -------------------------------------------------------------------------
    async def _async_update_data(self):
        """Main loop executed every 5 seconds."""

        # 1. Read sensors
        grid_state = self.hass.states.get(self.config[CONF_GRID_SENSOR])
        solar_state = self.hass.states.get(self.config[CONF_SOLAR_SENSOR])
        ac_state = self.hass.states.get(self.config[CONF_AC_POWER_SENSOR])

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
        except ValueError:
            _LOGGER.debug("Non-numeric sensor value, skipping cycle")
            return

        _LOGGER.debug(
            "Cycle sensors: grid_raw=%s solar=%s ac_power=%s", grid_raw, solar, ac_power
        )

        # 2. EMA updates (always safe & cheap)
        self._update_ema(grid_raw)

        # 3. Master switch handling (may schedule ON/OFF)
        await self._handle_master_switch(solar, ac_power)

        # 4. If master is OFF → skip all zone logic and manage OFF-state behavior
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
        self.export_margin = export - required_export

        self.last_add_conf = self._compute_add_conf(
            export=export,
            required_export=required_export,
            last_zone=last_zone,
        )
        self.last_remove_conf = self._compute_remove_conf(
            import_power=import_power,
            last_zone=last_zone,
        )

        # Unified signed confidence: positive → add, negative → remove
        self.confidence = self.last_add_conf - self.last_remove_conf

        now_ts = dt_util.utcnow().timestamp()

        # 7. Learning timeout (only if still active and master is ON)
        if self.learning_active and self.learning_start_time:
            if now_ts - self.learning_start_time >= 360:
                await self._log(f"[LEARNING_TIMEOUT] zone={self.learning_zone}")
                await self.controller.finish_learning()
                return

        # 8. Panic logic (grid import too high)
        if self._should_panic(on_count):
            await self._schedule_panic(active_zones)
            return

        # 9. Panic cooldown: avoid immediate re-add after shed
        if self._in_panic_cooldown(now_ts):
            self.last_action = "panic_cooldown"
            await self._log("[PANIC_COOLDOWN] skipping add/remove decisions")
            return

        # 10. Max-zones safety: never exceed hard cap
        if on_count >= _MAX_ZONES_DEFAULT:
            self.last_action = "max_zones_reached"
            await self._log(
                f"[MAX_ZONES_REACHED] on_count={on_count} max={_MAX_ZONES_DEFAULT}"
            )
            return

        # 11. ADD zone decision
        if next_zone and self._should_add_zone(next_zone, required_export):
            await self._attempt_add_zone(next_zone, ac_power, export, required_export)
            return

        # 12. REMOVE zone decision
        if last_zone and self._should_remove_zone(last_zone, import_power):
            await self._attempt_remove_zone(last_zone, import_power)
            return

        # 13. SYSTEM BALANCED
        self.last_action = "balanced"
        await self._log(
            f"[SYSTEM_BALANCED] ema30={round(self.ema_30s)} "
            f"ema5m={round(self.ema_5m)} zones={on_count} samples={self.samples}"
        )

    # -------------------------------------------------------------------------
    # EMA / metrics / guards
    # -------------------------------------------------------------------------
    def _update_ema(self, grid_raw: float) -> None:
        """Update short and long EMAs of grid power."""
        self.ema_30s = 0.25 * grid_raw + 0.75 * self.ema_30s
        self.ema_5m = 0.03 * grid_raw + 0.97 * self.ema_5m

    async def _handle_master_off_guard(self) -> bool:
        """Handle master off behavior: skip logic, cancel panic, reset learning, EMA reset."""
        ac_switch = self.config.get(CONF_AC_SWITCH)
        if not ac_switch:
            return False

        switch_state_obj = self.hass.states.get(ac_switch)
        if not switch_state_obj:
            return False

        if switch_state_obj.state != "off":
            # Master is ON → clear OFF tracking
            self.master_off_since = None
            return False

        # Master is OFF
        now_ts = dt_util.utcnow().timestamp()
        if self.master_off_since is None:
            self.master_off_since = now_ts

        # Cancel any in-flight panic task
        if self._panic_task and not self._panic_task.done():
            self._panic_task.cancel()
            self._panic_task = None

        # Reset learning state while master is off
        self.controller._reset_learning_state()

        # Optionally reset EMA after long OFF
        if now_ts - self.master_off_since >= _EMA_RESET_AFTER_OFF_SECONDS:
            if self.ema_30s != 0.0 or self.ema_5m != 0.0:
                await self._log(
                    "[EMA_RESET_AFTER_MASTER_OFF] resetting EMA due to long OFF period"
                )
            self.ema_30s = 0.0
            self.ema_5m = 0.0

        self.last_action = "master_off"
        await self._log("[MASTER_OFF] skipping all zone calculations")
        return True

    def _in_panic_cooldown(self, now_ts: float) -> bool:
        """Return True if we are still within cooldown period after panic."""
        if self.last_panic_ts is None:
            return False
        return (now_ts - self.last_panic_ts) < _PANIC_COOLDOWN_SECONDS

    # -------------------------------------------------------------------------
    # Zones, overrides, and short-cycling
    # -------------------------------------------------------------------------
    async def _update_zone_states_and_overrides(self) -> list[str]:
        """Build active zone list and update manual override and short-cycle memory."""
        active_zones: list[str] = []

        for zone in self.config[CONF_ZONES]:
            state_obj = self.hass.states.get(zone)
            if not state_obj:
                continue

            state = state_obj.state
            last_state = self.zone_last_state.get(zone)

            # Manual override detection: state change not caused by controller
            if last_state is not None and last_state != state:
                if not (
                    self.last_action
                    and (
                        self.last_action.endswith(zone)
                        or self.last_action == "panic"
                    )
                ):
                    now_ts = dt_util.utcnow().timestamp()
                    self.zone_manual_lock_until[zone] = now_ts + self.manual_lock_seconds
                    self.zone_last_changed[zone] = now_ts
                    self.zone_last_changed_type[zone] = (
                        "on" if state in ("heat", "on") else "off"
                    )
                    await self._log(
                        f"[MANUAL_OVERRIDE_DETECTED] zone={zone} state={state} "
                        f"lock_until={int(self.zone_manual_lock_until[zone])}"
                    )

            self.zone_last_state[zone] = state

            if state in ("heat", "on"):
                active_zones.append(zone)

        return active_zones

    def _is_locked(self, zone_id: str) -> bool:
        """Return True if zone is manually locked."""
        until = self.zone_manual_lock_until.get(zone_id)
        return bool(until and dt_util.utcnow().timestamp() < until)

    def _select_next_and_last_zone(
        self, active_zones: list[str]
    ) -> tuple[str | None, str | None]:
        """Select next candidate zone to add and last candidate zone to remove."""
        next_zone = next(
            (
                z
                for z in self.config[CONF_ZONES]
                if z not in active_zones and not self._is_locked(z)
            ),
            None,
        )

        last_zone = next(
            (z for z in reversed(active_zones) if not self._is_locked(z)),
            None,
        )

        return next_zone, last_zone

    def _compute_required_export(self, next_zone: str | None) -> float:
        """Compute required export margin for adding next_zone."""
        if not next_zone:
            return 99999.0

        zone_name = next_zone.split(".")[-1]
        lp = self.learned_power.get(zone_name, 1200)
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
        required_export: float,
        last_zone: str | None,
    ) -> float:
        """Compute confidence for adding a zone."""
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
        """Compute confidence for removing a zone."""
        base = min(60, max(0, (import_power - 200) / 8))
        heavy_import_bonus = 20 if import_power > 1500 else 0
        short_cycle_penalty = -40 if self._is_short_cycling(last_zone) else 0

        return base + 5 + heavy_import_bonus + short_cycle_penalty

    # -------------------------------------------------------------------------
    # Learning and panic
    # -------------------------------------------------------------------------
    def _should_panic(self, on_count: int) -> bool:
        """Return True if we should trigger panic shed."""
        return self.ema_30s > self.panic_threshold and on_count > 1

    async def _schedule_panic(self, active_zones: list[str]) -> None:
        """Schedule panic shed in the background if not already running."""
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
        """Turn off all but the first zone."""
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

            # Re-evaluate condition before acting
            if self.ema_30s > self.panic_threshold:
                await self._panic_shed(active_zones)

                # Reset learning state
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
    # Add / remove decisions (unified confidence axis)
    # -------------------------------------------------------------------------
    def _should_add_zone(self, next_zone: str, required_export: float) -> bool:
        """Return True if we should attempt to add next_zone."""
        if self.learning_active:
            return False

        # Require stable export on the 5m EMA side as well (no import)
        if self.ema_5m > -200:
            return False

        # Unified confidence: add only if strongly positive
        return self.confidence >= self.add_confidence_threshold

    def _should_remove_zone(self, last_zone: str, import_power: float) -> bool:
        """Return True if we should attempt to remove last_zone."""
        # Unified confidence: remove only if strongly negative
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
            _LOGGER.exception("Fallback climate.%s failed for %s: %s", service, entity_id, e)

    # -------------------------------------------------------------------------
    # Master switch control
    # -------------------------------------------------------------------------
    async def _handle_master_switch(self, solar: float, ac_power: float):
        """Master relay control based on solar availability and compressor safety."""
        ac_switch = self.config[CONF_AC_SWITCH]
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
                await self._log("[MASTER_POWER_ON] cleared manual locks after OFF period")

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

            ac_state = self.hass.states.get(self.config[CONF_AC_POWER_SENSOR])
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
