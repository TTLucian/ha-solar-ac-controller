from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from homeassistant.util import dt as dt_util


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities = [
        SolarACLearningBinarySensor(coordinator),
        SolarACPanicBinarySensor(coordinator),
        SolarACShortCycleBinarySensor(coordinator),
        SolarACLockedBinarySensor(coordinator),
        SolarACExportingBinarySensor(coordinator),
        SolarACImportingBinarySensor(coordinator),
    ]

    async_add_entities(entities)

class _BaseSolarACBinary(BinarySensorEntity):
    _attr_should_poll = False

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "solar_ac_controller")},
            "name": "Solar AC Controller",
            "manufacturer": "TTLucian",
            "model": "Solar AC Smart Controller",
            "sw_version": self.coordinator.config.get("version", "0.1.3"),
            "hw_version": "virtual",
            "configuration_url": "https://github.com/TTLucian/ha-solar-ac-controller",
            "suggested_area": "HVAC",
            "entry_type": "service",
        }

    async def async_added_to_hass(self):
        self.coordinator.async_add_listener(self.async_write_ha_state)

class _BaseSolarACBinary(BinarySensorEntity):
    _attr_should_poll = False

    def __init__(self, coordinator):
        self.coordinator = coordinator

    async def async_added_to_hass(self):
        self.coordinator.async_add_listener(self.async_write_ha_state)


class SolarACLearningBinarySensor(_BaseSolarACBinary):
    @property
    def name(self):
        return "Solar AC Learning Active"

    @property
    def unique_id(self):
        return "solar_ac_learning_active"

    @property
    def is_on(self):
        return self.coordinator.learning_active


class SolarACPanicBinarySensor(_BaseSolarACBinary):
    @property
    def name(self):
        return "Solar AC Panic State"

    @property
    def unique_id(self):
        return "solar_ac_panic_state"

    @property
    def is_on(self):
        return self.coordinator.last_action == "panic"


class SolarACShortCycleBinarySensor(_BaseSolarACBinary):
    @property
    def name(self):
        return "Solar AC Short Cycling"

    @property
    def unique_id(self):
        return "solar_ac_short_cycling"

    @property
    def is_on(self):
        c = self.coordinator
        for z in c.config["zones"]:
            last = c.zone_last_changed.get(z)
            if last and (dt_util.utcnow().timestamp() - last) < 1200:
                return True
        return False


class SolarACLockedBinarySensor(_BaseSolarACBinary):
    @property
    def name(self):
        return "Solar AC Manual Lock Active"

    @property
    def unique_id(self):
        return "solar_ac_manual_lock"

    @property
    def is_on(self):
        c = self.coordinator
        now = dt_util.utcnow().timestamp()
        return any(
            until and until > now
            for until in c.zone_manual_lock_until.values()
        )


class SolarACExportingBinarySensor(_BaseSolarACBinary):
    @property
    def name(self):
        return "Solar AC Exporting"

    @property
    def unique_id(self):
        return "solar_ac_exporting"

    @property
    def is_on(self):
        return self.coordinator.ema_30s < 0


class SolarACImportingBinarySensor(_BaseSolarACBinary):
    @property
    def name(self):
        return "Solar AC Importing"

    @property
    def unique_id(self):
        return "solar_ac_importing"

    @property
    def is_on(self):
        return self.coordinator.ema_30s > 0
