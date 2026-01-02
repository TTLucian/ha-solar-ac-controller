from __future__ import annotations
from homeassistant.util import dt as dt_util

import asyncio
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .controller import SolarACController
from .const import (
    CONF_SOLAR_SENSOR,
    CONF_GRID_SENSOR,
    CONF_AC_POWER_SENSOR,
    CONF_AC_SWITCH,
    CONF_ZONES,
    CONF_SOLAR_THRESHOLD_ON,
    CONF_SOLAR_THRESHOLD_OFF,
    CONF_PANIC_THRESHOLD,
    CONF_PANIC_DELAY,
    CONF_MANUAL_LOCK_SECONDS,
    CONF_SHORT_CYCLE_ON_SECONDS,
    CONF_SHORT_CYCLE_OFF_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


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
        # last change type per zone: 'on' or 'off'
        self.zone_last_changed_type: dict[str, str] = {}
        
        # Manual override detection (hands-off window)
        self.zone_last_state: dict[str, str | None] = {}
        self.zone_manual_lock_until: dict[str, float] = {}
        
        # Panic configuration (watts, positive = grid import)
        self.panic_threshold: float = config.get(CONF_PANIC_THRESHOLD, 1500)
        self.panic_delay: int = config.get(CONF_PANIC_DELAY, 10)
        # Manual lock duration (seconds) — configurable via options
        self.manual_lock_seconds: int = config.get(CONF_MANUAL_LOCK_SECONDS, 1200)
        # Short-cycle thresholds (different after on vs off)
        self.short_cycle_on_seconds: int = config.get(CONF_SHORT_CYCLE_ON_SECONDS, 1200)
        self.short_cycle_off_seconds: int = config.get(CONF_SHORT_CYCLE_OFF_SECONDS, 1200)
        # Delay between consecutive zone actions (seconds) to avoid hammering cloud APIs
        self.action_delay_seconds: int = config.get("action_delay_seconds", 3)

        # Controller
        self.controller = SolarACController(hass, self, store)
        # last computed confidences (exposed by sensors)
        self.last_add_conf: float = 0.0
        self.last_remove_conf: float = 0.0
        # last action timing for observability
        self.last_action_start_ts: float | None = None
        self.last_action_duration: float | None = None
        # background task handles
        self._panic_task: asyncio.Task | None = None
        self._master_shutdown_task: asyncio.Task | None = None

    async def _async_update_data(self):
        """Main loop executed every 5 seconds."""

        grid_state = self.hass.states.get(self.config[CONF_GRID_SENSOR])
        solar_state = self.hass.states.get(self.config[CONF_SOLAR_SENSOR])
        ac_state = self.hass.states.get(self.config[CONF_AC_POWER_SENSOR])

        if not grid_state or not solar_state or not ac_state:
            _LOGGER.debug("Missing sensor state, skipping cycle")
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

        # EMA updates
        self.ema_30s = 0.25 * grid_raw + 0.75 * self.ema_30s
        self.ema_5m = 0.03 * grid_raw + 0.97 * self.ema_5m

        # MASTER SWITCH CONTROL
        await self._handle_master_switch(solar, ac_power)

        # Determine active zones & detect manual overrides
        active_zones: list[str] = []
        for zone in self.config[CONF_ZONES]:
            state_obj = self.hass.states.get(zone)
            if not state_obj:
                continue
        
            state = state_obj.state
        
            # Manual override detection:
            last_state = self.zone_last_state.get(zone)
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
                    # record last change timestamp and whether it was turned on/off
                    self.zone_last_changed[zone] = now_ts
                    self.zone_last_changed_type[zone] = "on" if state in ("heat", "on") else "off"
                    await self._log(
                        f"[MANUAL_OVERRIDE_DETECTED] zone={zone} state={state} "
                        f"lock_until={int(self.zone_manual_lock_until[zone])}"
                    )
        
            self.zone_last_state[zone] = state
        
            if state in ("heat", "on"):
                active_zones.append(zone)

        on_count = len(active_zones)

        # Helper: zone is currently locked by manual override
        def _is_locked(zone_id: str) -> bool:
            until = self.zone_manual_lock_until.get(zone_id)
            return bool(until and dt_util.utcnow().timestamp() < until)
        
        # Next and last zone (controller-managed only, ignore locked)
        next_zone = next(
            (
                z
                for z in self.config[CONF_ZONES]
                if z not in active_zones and not _is_locked(z)
            ),
            None,
        )
        
        # last_zone is the last active zone that is not locked
        last_zone = next(
            (z for z in reversed(active_zones) if not _is_locked(z)), None
        )

        # Required export
        if next_zone:
            zone_name = next_zone.split(".")[-1]
            lp = self.learned_power.get(zone_name, 1200)
            safety_mult = 1.15 if self.samples >= 10 else 1.30
            required_export = lp * safety_mult
        else:
            required_export = 99999

        # ADD confidence
        export = -self.ema_30s
        export_margin = export - required_export
        add_conf = (
            min(40, max(0, export_margin / 25))
            + 5
            + min(20, self.samples * 2)
            + (-30 if self._is_short_cycling(last_zone) else 0)
        )
        self.last_add_conf = add_conf

        # REMOVE confidence
        import_power = self.ema_5m
        remove_conf = (
            min(60, max(0, (import_power - 200) / 8))
            + 5
            + (20 if import_power > 1500 else 0)
            + (-40 if self._is_short_cycling(last_zone) else 0)
        )
        self.last_remove_conf = remove_conf

        # ---------------------------------------------------------
        # IMPROVEMENT A: Learning timeout returns immediately
        # ---------------------------------------------------------
        if self.learning_active and self.learning_start_time:
            if dt_util.utcnow().timestamp() - self.learning_start_time >= 360:  # 6 minutes
                await self._log(f"[LEARNING_TIMEOUT] zone={self.learning_zone}")
                await self.controller.finish_learning()
                return   # <-- FIX: stop the cycle here

        # ---------------------------------------------------------
        # IMPROVEMENT B: Panic shed resets learning state
        # ---------------------------------------------------------
        if self.ema_30s > self.panic_threshold and on_count > 1:
            if self.last_action != "panic":
                await self._log(
                    f"[PANIC_SHED_TRIGGER] ema30={round(self.ema_30s)} "
                    f"ema5m={round(self.ema_5m)} threshold={self.panic_threshold} "
                    f"zones={active_zones}"
                )
                # schedule background panic handler to avoid blocking the coordinator
                if not self._panic_task or self._panic_task.done():
                    self._panic_task = self.hass.async_create_task(
                        self._panic_task_runner(active_zones)
                    )
            return

        # ZONE ADD
        if next_zone and add_conf >= 25 and not self.learning_active:
            if self.last_action != f"add_{next_zone}":
                await self._log(
                    f"[ZONE_ADD_ATTEMPT] zone={next_zone} "
                    f"add_conf={round(add_conf)} export={round(export)} "
                    f"req_export={round(required_export)} samples={self.samples}"
                )
                await self._add_zone(next_zone, ac_power)
                self.last_action = f"add_{next_zone}"
            return

        # ZONE REMOVE
        if last_zone and remove_conf >= 40:
            if self.last_action != f"remove_{last_zone}":
                await self._log(
                    f"[ZONE_REMOVE_ATTEMPT] zone={last_zone} "
                    f"remove_conf={round(remove_conf)} import={round(import_power)} "
                    f"short_cycling={self._is_short_cycling(last_zone)}"
                )
                await self._remove_zone(last_zone)
                self.last_action = f"remove_{last_zone}"
            return

        # SYSTEM BALANCED
        self.last_action = "balanced"
        await self._log(
            f"[SYSTEM_BALANCED] ema30={round(self.ema_30s)} ema5m={round(self.ema_5m)} "
            f"zones={on_count} samples={self.samples}"
        )

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

    async def _add_zone(self, zone: str, ac_power_before: float):
        """Start learning + turn on zone."""
        if self.learning_active:
            await self._log(
                f"[LEARNING_SKIPPED_ALREADY_ACTIVE] zone={zone} "
                f"current_zone={self.learning_zone}"
            )
            return
        
        await self.controller.start_learning(zone, ac_power_before)

        # Support multiple entity domains (climate, switch, fan, etc.)
        start = dt_util.utcnow().timestamp()
        try:
            await self._call_entity_service(zone, True)
        finally:
            # Record timing and short-cycle info regardless of success
            now_ts = dt_util.utcnow().timestamp()
            self.last_action_start_ts = start
            self.last_action_duration = now_ts - start
            self.zone_last_changed[zone] = now_ts
            self.zone_last_changed_type[zone] = "on"

        # small delay between sequential actions to avoid hammering cloud APIs
        await asyncio.sleep(self.action_delay_seconds)

        await self._log(
            f"[LEARNING_START] zone={zone} ac_before={round(ac_power_before)} "
            f"samples={self.samples}"
        )

    async def _remove_zone(self, zone: str):
        # Support multiple entity domains (climate, switch, fan, etc.)
        start = dt_util.utcnow().timestamp()
        try:
            await self._call_entity_service(zone, False)
        finally:
            now_ts = dt_util.utcnow().timestamp()
            self.last_action_start_ts = start
            self.last_action_duration = now_ts - start
            self.zone_last_changed[zone] = now_ts
            self.zone_last_changed_type[zone] = "off"

        # small delay between sequential actions to avoid hammering cloud APIs
        await asyncio.sleep(self.action_delay_seconds)

        await self._log(
            f"[ZONE_REMOVE_SUCCESS] zone={zone} import_after={round(self.ema_5m)}"
        )

    async def _call_entity_service(self, entity_id: str, turn_on: bool):
        """Call an appropriate turn_on/turn_off service for the entity's domain.

        Falls back to `climate` domain if the primary domain service fails.
        """
        domain = entity_id.split(".")[0]
        service = "turn_on" if turn_on else "turn_off"

        try:
            await self.hass.services.async_call(domain, service, {"entity_id": entity_id}, blocking=True)
            return
        except Exception as e:
            _LOGGER.debug("Primary service %s.%s failed for %s: %s", domain, service, entity_id, e)

        # Fallback to climate service (common for HVAC entities)
        try:
            await self.hass.services.async_call("climate", service, {"entity_id": entity_id}, blocking=True)
            _LOGGER.warning("Primary service %s.%s failed for %s — used climate.%s as fallback", domain, service, entity_id, service)
            return
        except Exception as e:
            _LOGGER.exception("Fallback climate.%s failed for %s: %s", service, entity_id, e)

    async def _panic_shed(self, active_zones: list[str]):
        """Turn off all but the first zone."""
        start = dt_util.utcnow().timestamp()
        for zone in active_zones[1:]:
            await self._call_entity_service(zone, False)
            # wait between consecutive actions to avoid hammering cloud APIs
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

                # reset learning state
                self.learning_active = False
                self.learning_zone = None
                self.learning_start_time = None
                self.ac_power_before = None

                await self._log(
                    f"[PANIC_SHED] ema30={round(self.ema_30s)} "
                    f"ema5m={round(self.ema_5m)} zones={active_zones}"
                )

                self.last_action = "panic"
        except Exception as e:
            _LOGGER.exception("Error in panic task: %s", e)
        finally:
            self._panic_task = None

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
                "switch", "turn_on", {"entity_id": ac_switch}, blocking=True
            )

        # Turn OFF when solar is low and AC is idle
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
            except ValueError:
                ac_now = 0.0

            if ac_now < 25:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": ac_switch}, blocking=True
                )
                await self._log(
                    f"[MASTER_POWER_OFF] ac={round(ac_now)}W"
                )
            else:
                await self._log(
                    f"[MASTER_SHUTDOWN_BLOCKED] ac={round(ac_now)}W"
                )
        except Exception as e:
            _LOGGER.exception("Error in delayed master shutdown: %s", e)
        finally:
            self._master_shutdown_task = None

    async def _log(self, message: str):
        """Log to HA logbook with a consistent taxonomy."""
        # also log to python logger for developers
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
