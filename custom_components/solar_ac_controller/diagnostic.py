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

        # Active zones (direct from HA state)
        active_zones = [
            z for z in c.config["zones"]
            if (st := c.hass.states.get(z)) and st.state in ("heat", "on")
        ]

        # Panic cooldown
        panic_cooldown_active = False
        if c.last_panic_ts:
            panic_cooldown_active = (now - c.last_panic_ts) < 120

        return {
            # Timestamp
            "timestamp": now,

            # Config
            "config": c.config,

            # Learning
            "learning_active": c.learning_active,
            "learning_zone": c.learning_zone,
            "learning_start_time": c.learning_start_time,
            "samples": c.samples,
            "learned_power": c.learned_power,
            "ac_power_before": c.ac_power_before,

            # EMA metrics
            "ema_30s": c.ema_30s,
            "ema_5m": c.ema_5m,

            # Decision state
            "last_action": c.last_action,
            "next_zone": c.next_zone,
            "last_zone": c.last_zone,
            "required_export": c.required_export,
            "export_margin": c.export_margin,

            # Zones
            "active_zones": active_zones,
            "zone_last_changed": c.zone_last_changed,
            "zone_last_state": c.zone_last_state,
            "zone_manual_lock_until": c.zone_manual_lock_until,

            # Panic
            "panic_threshold": c.panic_threshold,
            "panic_delay": c.panic_delay,
            "last_panic_ts": c.last_panic_ts,
            "panic_cooldown_active": panic_cooldown_active,

            # Master switch
            "master_off_since": c.master_off_since,
        }
