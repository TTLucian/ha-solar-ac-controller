from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util
from .const import DOMAIN, CONF_ENABLE_DIAGNOSTICS

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    # NEW: respect the diagnostics toggle
    if not entry.options.get(CONF_ENABLE_DIAGNOSTICS, True):
        return

    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    async_add_entities([SolarACDiagnosticEntity(coordinator, entry)])

class SolarACDiagnosticEntity(SensorEntity):
    """A single sensor exposing the entire controller state as JSON attributes."""

    _attr_should_poll = False
    _attr_name = "Solar AC Diagnostics"
    _attr_icon = "mdi:brain"
    _attr_device_class = "diagnostic"

    def __init__(self, coordinator, entry: ConfigEntry):
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_diagnostics"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, "solar_ac_controller")},
            "name": "Solar AC Controller",
            "manufacturer": "TTLucian",
            "model": "Solar AC Smart Controller",
            "configuration_url": "https://github.com/TTLucian/ha-solar-ac-controller",
        }

    async def async_added_to_hass(self):
        """Register for coordinator updates."""
        self._unsub = self.coordinator.async_add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        """Clean up listener."""
        if hasattr(self, "_unsub"):
            self._unsub()

    @property
    def native_value(self):
        """Expose last action as the main state."""
        return self.coordinator.last_action or "idle"

from .helpers import build_diagnostics

@property
def extra_state_attributes(self):
    return build_diagnostics(self.coordinator)

