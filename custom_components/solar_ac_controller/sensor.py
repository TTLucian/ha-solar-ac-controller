from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
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

    entities = [
        SolarACActiveZonesSensor(coordinator),
        SolarACNextZoneSensor(coordinator),
        SolarACLastZoneSensor(coordinator),
        SolarACLastActionSensor(coordinator),

        SolarACEma30Sensor(coordinator),
        SolarACEma5Sensor(coordinator),

        SolarACConfidenceSensor(coordinator),
        SolarACConfidenceThresholdSensor(coordinator),

        SolarACRequiredExportSensor(coordinator),
        SolarACExportMarginSensor(coordinator),
        SolarACImportPowerSensor(coordinator),

        SolarACMasterOffSinceSensor(coordinator),
        SolarACLastPanicSensor(coordinator),
        SolarACPanicCooldownSensor(coordinator),
    ]

    # Learned power sensors (one per zone)
    for zone in coordinator.config["zones"]:
        zone_name = zone.split(".")[-1]
        entities.append(SolarACLearnedPowerSensor(coordinator, zone_name))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# BASE CLASS
# ---------------------------------------------------------------------------

class _BaseSolarACSensor(SensorEntity):
    """Base class for all Solar AC sensors."""

    _attr_should_poll = False

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "solar_ac_controller")},
            "name": "Solar AC Controller",
            "configuration_url": "https://github.com/TTLucian/ha-solar-ac-controller",
        }

    @property
    def available(self):
        return True

    async def async_added_to_hass(self):
        self.coordinator.async_add_listener(self.async_write_ha_state)


# ---------------------------------------------------------------------------
# NON-NUMERIC SENSORS
# ---------------------------------------------------------------------------

class SolarACActiveZonesSensor(_BaseSolarACSensor):
    @property
    def name(self):
        return "Solar AC Active Zones"

    @property
    def unique_id(self):
        return "solar_ac_active_zones"

    @property
    def state(self):
        zones = []
        for z in self.coordinator.config["zones"]:
            st = self.coordinator.hass.states.get(z)
            if st and st.state in ("heat", "on"):
                zones.append(z)
        return ", ".join(zones) if zones else "none"


class SolarACNextZoneSensor(_BaseSolarACSensor):
    @property
    def name(self):
        return "Solar AC Next Zone"

    @property
    def unique_id(self):
        return "solar_ac_next_zone"

    @property
    def state(self):
        return self.coordinator.next_zone or "none"


class SolarACLastZoneSensor(_BaseSolarACSensor):
    @property
    def name(self):
        return "Solar AC Last Zone"

    @property
    def unique_id(self):
        return "solar_ac_last_zone"

    @property
    def state(self):
        return self.coordinator.last_zone or "none"


class SolarACLastActionSensor(_BaseSolarACSensor):
    @property
    def name(self):
        return "Solar AC Last Action"

    @property
    def unique_id(self):
        return "solar_ac_last_action"

    @property
    def state(self):
        return self.coordinator.last_action or "none"


# ---------------------------------------------------------------------------
# NUMERIC SENSOR BASE CLASS
# ---------------------------------------------------------------------------

class _NumericSolarACSensor(_BaseSolarACSensor):
    """Base class for numeric sensors with proper metadata."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "W"


# ---------------------------------------------------------------------------
# NUMERIC SENSORS
# ---------------------------------------------------------------------------

class SolarACEma30Sensor(_NumericSolarACSensor):
    @property
    def name(self):
        return "Solar AC EMA 30s"

    @property
    def unique_id(self):
        return "solar_ac_ema_30s"

    @property
    def state(self):
        return round(self.coordinator.ema_30s, 2)


class SolarACEma5Sensor(_NumericSolarACSensor):
    @property
    def name(self):
        return "Solar AC EMA 5m"

    @property
    def unique_id(self):
        return "solar_ac_ema_5m"

    @property
    def state(self):
        return round(self.coordinator.ema_5m, 2)


class SolarACConfidenceSensor(_BaseSolarACSensor):
    """Dimensionless numeric confidence value."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "pts"
    _attr_device_class = None  # confidence is not power

    @property
    def name(self):
        return "Solar AC Confidence"

    @property
    def unique_id(self):
        return "solar_ac_confidence"

    @property
    def state(self):
        return round(self.coordinator.confidence, 2)


class SolarACConfidenceThresholdSensor(_BaseSolarACSensor):
    @property
    def name(self):
        return "Solar AC Confidence Thresholds"

    @property
    def unique_id(self):
        return "solar_ac_conf_thresholds"

    @property
    def state(self):
        return "ok"

    @property
    def extra_state_attributes(self):
        return {
            "add_threshold": self.coordinator.add_confidence_threshold,
            "remove_threshold": self.coordinator.remove_confidence_threshold,
        }


class SolarACRequiredExportSensor(_NumericSolarACSensor):
    @property
    def state(self):
        val = self.coordinator.required_export
        return None if val is None else round(val, 2)

    @property
    def unique_id(self):
        return "solar_ac_required_export"

    @property
    def state(self):
        return round(self.coordinator.required_export, 2)


class SolarACExportMarginSensor(_NumericSolarACSensor):
    @property
    def name(self):
        return "Solar AC Export Margin"

    @property
    def unique_id(self):
        return "solar_ac_export_margin"

    @property
    def state(self):
        return round(self.coordinator.export_margin, 2)


class SolarACImportPowerSensor(_NumericSolarACSensor):
    @property
    def name(self):
        return "Solar AC Import Power"

    @property
    def unique_id(self):
        return "solar_ac_import_power"

    @property
    def state(self):
        return round(self.coordinator.ema_5m, 2)


class SolarACMasterOffSinceSensor(_NumericSolarACSensor):
    @property
    def name(self):
        return "Solar AC Master Off Since"

    @property
    def unique_id(self):
        return "solar_ac_master_off_since"

    @property
    def state(self):
        return int(self.coordinator.master_off_since or 0)


class SolarACLastPanicSensor(_NumericSolarACSensor):
    @property
    def name(self):
        return "Solar AC Last Panic"

    @property
    def unique_id(self):
        return "solar_ac_last_panic"

    @property
    def state(self):
        return int(self.coordinator.last_panic_ts or 0)


class SolarACPanicCooldownSensor(_BaseSolarACSensor):
    @property
    def name(self):
        return "Solar AC Panic Cooldown Active"

    @property
    def unique_id(self):
        return "solar_ac_panic_cooldown"

    @property
    def state(self):
        now = dt_util.utcnow().timestamp()
        ts = self.coordinator.last_panic_ts
        if not ts:
            return "no"
        return "yes" if (now - ts) < 120 else "no"


# ---------------------------------------------------------------------------
# LEARNED POWER SENSOR
# ---------------------------------------------------------------------------

class SolarACLearnedPowerSensor(_NumericSolarACSensor):
    def __init__(self, coordinator, zone_name):
        super().__init__(coordinator)
        self.zone_name = zone_name

    @property
    def name(self):
        return f"Solar AC Learned Power {self.zone_name}"

    @property
    def unique_id(self):
        return f"solar_ac_learned_power_{self.zone_name}"

    @property
    def state(self):
        return self.coordinator.learned_power.get(
            self.zone_name,
            self.coordinator.initial_learned_power,
        )
