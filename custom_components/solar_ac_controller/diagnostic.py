from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    async_add_entities([SolarACDiagnosticEntity(coordinator)])


class SolarACDiagnosticEntity(SensorEntity):
    """A single entity exposing the entire controller brain."""

    _attr_should_poll = False
    _attr_name = "Solar AC Diagnostics"
    _attr_unique_id = "solar_ac_diagnostics"

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "solar_ac_controller")},
            "name": "Solar AC Controller",
            "configuration_url": "https://github.com/TTLucian/ha-solar-ac-controller",
            "suggested_area": "HVAC",
            "entry_type": "service",
        }

    async def async_added_to_hass(self):
        self.coordinator.async_add_listener(self.async_write_ha_state)

    @property
    def state(self):
        return self.coordinator.last_action or "idle"

    @property
    def extra_state_attributes(self):
        c = self.coordinator
        now = dt_util.utcnow().timestamp()

        active_zones = []
        for z in c.config["zones"]:
            st = c.hass.states.get(z)
            if st and st.state in ("heat", "on"):
                active_zones.append(z)

        return {
            "config": c.config,
            "active_zones": active_zones,
            "learning_active": c.learning_active,
            "learning_zone": c.learning_zone,
            "learning_start_time": c.learning_start_time,
            "samples": c.samples,
            "learned_power": c.learned_power,
            "ema_30s": c.ema_30s,
            "ema_5m": c.ema_5m,
            "zone_last_changed": c.zone_last_changed,
            "zone_last_state": c.zone_last_state,
            "zone_manual_lock_until": c.zone_manual_lock_until,
            "panic_threshold": c.panic_threshold,
            "panic_delay": c.panic_delay,
            "last_action": c.last_action,
        }
