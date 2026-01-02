from __future__ import annotations

import asyncio
import logging
import time
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
        
        # Manual override detection (hands-off window)
        self.zone_last_state: dict[str, str | None] = {}
        self.zone_manual_lock_until: dict[str, float] = {}
        
        # Panic configuration
        self.panic_threshold: float = config.get(CONF_PANIC_THRESHOLD, 2500)
        self.panic_delay: int = config.get(CONF_PANIC_DELAY, 10)


        # Controller
        self.controller = SolarACController(hass, self, store)

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
                    self.zone_manual_lock_until[zone] = time.time() + 1200  # 20 minutes
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
            return bool(until and time.time() < until)
        
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

        # REMOVE confidence
        import_power = self.ema_5m
        remove_conf = (
            min(60, max(0, (import_power - 200) / 8))
            + 5
            + (20 if import_power > 1500 else 0)
            + (-40 if self._is_short_cycling(last_zone) else 0)
        )

        # Learning completion
        if self.learning_active and self.learning_start_time:
            if time.time() - self.learning_start_time >= 360:  # 6 minutes
                await self._log(f"[LEARNING_TIMEOUT] zone={self.learning_zone}")
                await self.controller.finish_learning()

        # PANIC SHED (now based on EMA and configurable threshold)
        if self.ema_30s > self.panic_threshold and on_count > 1:
            if self.last_action != "panic":
                await self._log(
                    f"[PANIC_SHED_TRIGGER] ema30={round(self.ema_30s)} "
                    f"ema5m={round(self.ema_5m)} threshold={self.panic_threshold} "
                    f"zones={active_zones}"
                )
                # Optional debounce before actually shedding
                if self.panic_delay > 0:
                    await asyncio.sleep(self.panic_delay)
                # Re-check condition after delay
                if self.ema_30s > self.panic_threshold:
                    await self._panic_shed(active_zones)
                    await self._log(
                        f"[PANIC_SHED] ema30={round(self.ema_30s)} "
                        f"ema5m={round(self.ema_5m)} zones={active_zones}"
                    )
                self.last_action = "panic"
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
        return (time.time() - last) < 1200  # 20 minutes

    async def _add_zone(self, zone: str, ac_power_before: float):
        """Start learning + turn on zone."""
        # Do not start learning if another session is active
        if self.learning_active:
            await self._log(
                f"[LEARNING_SKIPPED_ALREADY_ACTIVE] zone={zone} "
                f"current_zone={self.learning_zone}"
            )
            return
        
        await self.controller.start_learning(zone, ac_power_before)

        await self.hass.services.async_call(
            "climate", "turn_on", {"entity_id": zone}, blocking=True
        )

        self.zone_last_changed[zone] = time.time()

        await self._log(
            f"[LEARNING_START] zone={zone} ac_before={round(ac_power_before)} "
            f"samples={self.samples}"
        )

    async def _remove_zone(self, zone: str):
        await self.hass.services.async_call(
            "climate", "turn_off", {"entity_id": zone}, blocking=True
        )
        self.zone_last_changed[zone] = time.time()

        await self._log(
            f"[ZONE_REMOVE_SUCCESS] zone={zone} import_after={round(self.ema_5m)}"
        )

    async def _panic_shed(self, active_zones: list[str]):
        """Turn off all but the first zone."""
        for zone in active_zones[1:]:
            await self.hass.services.async_call(
                "climate", "turn_off", {"entity_id": zone}, blocking=True
            )
            await asyncio.sleep(3)

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
                "waiting 10 minutes for compressor safety"
            )
            await asyncio.sleep(600)

            ac_state = self.hass.states.get(self.config[CONF_AC_POWER_SENSOR])
            try:
                ac_now = float(ac_state.state) if ac_state else ac_power
            except ValueError:
                ac_now = ac_power

            if ac_now < 25:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": ac_switch}, blocking=True
                )
                await self._log(
                    f"[MASTER_POWER_OFF] solar={round(solar)}W ac={round(ac_now)}W"
                )
            else:
                await self._log(
                    f"[MASTER_SHUTDOWN_BLOCKED] solar={round(solar)}W ac={round(ac_now)}W"
                )

    async def _log(self, message: str):
        """Log to HA logbook with a consistent taxonomy."""
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
