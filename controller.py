from __future__ import annotations

import time
from homeassistant.core import HomeAssistant


class SolarACController:
    """Learning engine + state transitions for Solar AC."""

    def __init__(self, hass: HomeAssistant, coordinator, store):
        self.hass = hass
        self.coordinator = coordinator
        self.store = store

    async def start_learning(self, zone, ac_power_before):
        """Mark learning as active and store initial state."""
        c = self.coordinator

        c.learning_active = True
        c.learning_start_time = time.time()
        c.ac_power_before = ac_power_before
        c.learning_zone = zone

    async def finish_learning(self):
        """Finish learning after stabilization delay."""
        c = self.coordinator

        if not c.learning_active:
            return

        # Read current AC power
        ac_power_now = float(
            self.hass.states.get(self.coordinator.config["ac_power_sensor"]).state
        )

        delta = ac_power_now - c.ac_power_before
        zone_name = c.learning_zone.split(".")[-1]

        # Validate delta
        if 250 < delta < 2500:
            prev = c.learned_power.get(zone_name, 1200)
            new_value = round(prev * 0.7 + delta * 0.3)

            c.learned_power[zone_name] = new_value
            c.samples += 1

            await self._save()
        # else: skip learning

        # Reset learning state
        c.learning_active = False
        c.learning_zone = None
        c.learning_start_time = None
        c.ac_power_before = None

    async def _save(self):
        """Persist learned values to HA storage."""
        await self.store.async_save(
            {
                "learned_power": self.coordinator.learned_power,
                "samples": self.coordinator.samples,
            }
        )

    async def reset_learning(self):
        """Reset all learned values."""
        c = self.coordinator

        for zone in c.config["zones"]:
            zone_name = zone.split(".")[-1]
            c.learned_power[zone_name] = 1200

        c.samples = 0

        await self._save()
