from .controller import SolarACController

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, STORAGE_KEY


class SolarACCoordinator(DataUpdateCoordinator):
    """Main control loop for the Solar AC Controller."""

    def __init__(self, hass: HomeAssistant, config, store, stored):
        super().__init__(
            hass,
            logger=hass.logger,
            name="Solar AC Controller",
            update_interval=timedelta(seconds=5),
        )

        self.hass = hass
        self.config = config
        self.store = store

        # Load learned values from storage
        self.learned_power = stored.get("learned_power", {})
        self.samples = stored.get("samples", 0)

        # Internal state
        self.last_action = None
        self.learning_active = False
        self.learning_start_time = None
        self.ac_power_before = None

        # EMA state
        self.ema_30s = 0
        self.ema_5m = 0

        # Short cycle memory
        self.zone_last_changed = {}

    async def _async_update_data(self):
        """Main loop executed every 5 seconds."""

        grid_raw = float(self.hass.states.get(self.config["grid_sensor"]).state)
        solar = float(self.hass.states.get(self.config["solar_sensor"]).state)
        ac_power = float(self.hass.states.get(self.config["ac_power_sensor"]).state)

        # Update EMA 30s
        self.ema_30s = 0.25 * grid_raw + 0.75 * self.ema_30s

        # Update EMA 5m
        self.ema_5m = 0.03 * grid_raw + 0.97 * self.ema_5m

        # Determine active zones
        active_zones = []
        for zone in self.config["zones"]:
            state = self.hass.states.get(zone).state
            if state in ("heat", "on"):
                active_zones.append(zone)

        on_count = len(active_zones)

        # Determine next and last zone
        next_zone = next((z for z in self.config["zones"] if z not in active_zones), None)
        last_zone = active_zones[-1] if active_zones else None

        # Compute required export
        if next_zone:
            zone_name = next_zone.split(".")[-1]
            lp = self.learned_power.get(zone_name, 1200)
            safety_mult = 1.15 if self.samples >= 10 else 1.30
            required_export = lp * safety_mult
        else:
            required_export = 99999

        # Compute ADD confidence
        export = -self.ema_30s
        export_margin = export - required_export
        add_conf = (
            min(40, max(0, export_margin / 25))
            + 5
            + min(20, self.samples * 2)
            + (-30 if self._is_short_cycling(last_zone) else 0)
        )

        # Compute REMOVE confidence
        import_power = self.ema_5m
        remove_conf = (
            min(60, max(0, (import_power - 200) / 8))
            + 5
            + (20 if import_power > 1500 else 0)
            + (-40 if self._is_short_cycling(last_zone) else 0)
        )

        # PANIC SHED
        if grid_raw > 2500 and on_count > 1:
            if self.last_action != "panic":
                await self._panic_shed(active_zones)
                self.last_action = "panic"
            return

        # ZONE ADD
        if next_zone and add_conf >= 25 and not self.learning_active:
            if self.last_action != f"add_{next_zone}":
                await self._add_zone(next_zone, ac_power)
                self.last_action = f"add_{next_zone}"
            return

        # ZONE REMOVE
        if last_zone and remove_conf >= 40:
            if self.last_action != f"remove_{last_zone}":
                await self._remove_zone(last_zone)
                self.last_action = f"remove_{last_zone}"
            return

        # SYSTEM BALANCED
        self.last_action = "balanced"

    def _is_short_cycling(self, zone):
        if not zone:
            return False
        last = self.zone_last_changed.get(zone)
        if not last:
            return False
        return (time.time() - last) < 1200  # 20 minutes

    async def _add_zone(self, zone, ac_power_before):
        """Start learning + turn on zone."""
        self.learning_active = True
        self.learning_start_time = time.time()
        self.ac_power_before = ac_power_before

        await self.hass.services.async_call(
            "climate", "turn_on", {"entity_id": zone}
        )

        self.zone_last_changed[zone] = time.time()

    async def _remove_zone(self, zone):
        await self.hass.services.async_call(
            "climate", "turn_off", {"entity_id": zone}
        )
        self.zone_last_changed[zone] = time.time()

    async def _panic_shed(self, active_zones):
        """Turn off all but the first zone."""
        for zone in active_zones[1:]:
            await self.hass.services.async_call(
                "climate", "turn_off", {"entity_id": zone}
            )
            await asyncio.sleep(3)
