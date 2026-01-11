from __future__ import annotations

from typing import Callable

from homeassistant.components.sensor import SensorEntity

from .const import DOMAIN
from .helpers import build_diagnostics


class SolarACDiagnosticEntity(SensorEntity):
    """A single sensor exposing the entire controller state as JSON attributes."""

    _attr_should_poll = False
    _attr_name = "Solar AC Diagnostics"
    _attr_icon = "mdi:brain"
    # Omit device_class to avoid using a non-standard value

    def __init__(self, coordinator):
        self.coordinator = coordinator
        # Stable unique id not tied to config entry
        self._attr_unique_id = "solar_ac_diagnostics"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, "solar_ac_controller")},
            "name": "Solar AC Controller",
            "manufacturer": "TTLucian",
            "model": "Solar AC Smart Controller",
            "configuration_url": "https://github.com/TTLucian/ha-solar-ac-controller",
            "sw_version": getattr(coordinator, "version", None),
        }

        self._unsub: Callable[[], None] | None = None

    async def async_added_to_hass(self):
        """Register for coordinator updates and keep unsubscribe handle."""
        self._unsub = self.coordinator.async_add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        """Clean up listener."""
        if callable(self._unsub):
            try:
                self._unsub()
            except Exception:
                # Best-effort cleanup; don't raise during unload
                pass
            finally:
                self._unsub = None

    @property
    def native_value(self):
        """Expose last action as the main state."""
        return self.coordinator.last_action or "idle"

    @property
    def extra_state_attributes(self):
        """Expose unified diagnostics attributes."""
        return build_diagnostics(self.coordinator)
