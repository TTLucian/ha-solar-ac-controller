from __future__ import annotations

from homeassistant.helpers.entity import Entity


class SolarACDebugEntity(Entity):
    """Debug sensor exposing internal controller state."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_name = "Solar AC Controller Debug"
        self._attr_unique_id = "solar_ac_controller_debug"

    @property
    def should_poll(self) -> bool:
        return False

    @property
    def state(self):
        """Expose a simple state value."""
        # State = number of active zones
        c = self.coordinator
        return len(
            [
                z
                for z in c.config["zones"]
                if self.coordinator.hass.states.get(z)
                and self.coordinator.hass.states.get(z).state in ("heat", "on")
            ]
        )

    @property
    def extra_state_attributes(self):
        """Return full internal state for debugging."""
        c = self.coordinator

        # Learning confidence: 0.0 → 1.0
        confidence = min(1.0, c.samples / 10) if c.samples else 0.0

        return {
            "last_action": c.last_action,
            "learning_active": c.learning_active,
            "learning_zone": c.learning_zone,
            "learning_confidence": confidence,
            "samples": c.samples,
            "ema_30s": round(c.ema_30s, 2),
            "ema_5m": round(c.ema_5m, 2),
            "learned_power": c.learned_power,
            "zone_last_changed": c.zone_last_changed,
            "zone_manual_lock_until": c.zone_manual_lock_until,
        }

    async def async_added_to_hass(self):
        """Register for coordinator updates."""
        self.coordinator.async_add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        """Cleanup listener."""
        self.coordinator.async_remove_listener(self.async_write_ha_state)
