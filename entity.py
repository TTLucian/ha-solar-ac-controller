from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity


class SolarACDebugEntity(CoordinatorEntity, SensorEntity):
    """Debug sensor exposing internal controller state."""

    _attr_name = "Solar AC Controller Debug"
    _attr_icon = "mdi:solar-power"
    _attr_native_unit_of_measurement = None

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.coordinator = coordinator

    @property
    def unique_id(self):
        return "solar_ac_controller_debug"

    @property
    def native_value(self):
        """Return a compact summary string."""
        c = self.coordinator

        return (
            f"action={c.last_action}, "
            f"learning={c.learning_active}, "
            f"samples={c.samples}, "
            f"ema30={round(c.ema_30s)}, "
            f"ema5m={round(c.ema_5m)}"
        )

    @property
    def extra_state_attributes(self):
        """Return full internal state."""
        c = self.coordinator

        return {
            "last_action": c.last_action,
            "learning_active": c.learning_active,
            "samples": c.samples,
            "ema_30s": c.ema_30s,
            "ema_5m": c.ema_5m,
            "learned_power": c.learned_power,
            "zone_last_changed": c.zone_last_changed,
        }
