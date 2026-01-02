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

    entities = [
        SolarACActiveZonesSensor(coordinator),
        SolarACNextZoneSensor(coordinator),
        SolarACLastActionSensor(coordinator),
        SolarACEma30Sensor(coordinator),
        SolarACEma5Sensor(coordinator),
        SolarACAddConfidenceSensor(coordinator),
        SolarACRemoveConfidenceSensor(coordinator),
        SolarACRequiredExportSensor(coordinator),
        SolarACExportMarginSensor(coordinator),
        SolarACImportPowerSensor(coordinator),
    ]

    # Learned power sensors (one per zone)
    for zone in coordinator.config["zones"]:
        zone_name = zone.split(".")[-1]
        entities.append(SolarACLearnedPowerSensor(coordinator, zone_name))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# BASE CLASS (merged, final, correct)
# ---------------------------------------------------------------------------

class _BaseSolarACSensor(SensorEntity):
    """Base class for all Solar AC sensors."""

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

    @property
    def available(self):
        return True

    async def async_added_to_hass(self):
        self.coordinator.async_add_listener(self.async_write_ha_state)


# ---------------------------------------------------------------------------
# SENSOR ENTITIES
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
        c = self.coordinator
        now = dt_util.utcnow().timestamp()

        for z in c.config["zones"]:
            st = c.hass.states.get(z)
            if not st or st.state not in ("heat", "on"):
                lock = c.zone_manual_lock_until.get(z)
                if lock and lock > now:
                    continue
                return z
        return "none"


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


class SolarACEma30Sensor(_BaseSolarACSensor):
    @property
    def name(self):
        return "Solar AC EMA 30s"

    @property
    def unique_id(self):
        return "solar_ac_ema_30s"

    @property
    def state(self):
        return round(self.coordinator.ema_30s, 2)


class SolarACEma5Sensor(_BaseSolarACSensor):
    @property
    def name(self):
        return "Solar AC EMA 5m"

    @property
    def unique_id(self):
        return "solar_ac_ema_5m"

    @property
    def state(self):
        return round(self.coordinator.ema_5m, 2)


class SolarACAddConfidenceSensor(_BaseSolarACSensor):
    @property
    def name(self):
        return "Solar AC Add Confidence"

    @property
    def unique_id(self):
        return "solar_ac_add_conf"

    @property
    def state(self):
        return getattr(self.coordinator, "last_add_conf", None)


class SolarACRemoveConfidenceSensor(_BaseSolarACSensor):
    @property
    def name(self):
        return "Solar AC Remove Confidence"

    @property
    def unique_id(self):
        return "solar_ac_remove_conf"

    @property
    def state(self):
        return getattr(self.coordinator, "last_remove_conf", None)


class SolarACRequiredExportSensor(_BaseSolarACSensor):
    @property
    def name(self):
        return "Solar AC Required Export"

    @property
    def unique_id(self):
        return "solar_ac_required_export"

    @property
    def state(self):
        c = self.coordinator
        now = dt_util.utcnow().timestamp()

        next_zone = None
        for z in c.config["zones"]:
            st = c.hass.states.get(z)
            if not st or st.state not in ("heat", "on"):
                lock = c.zone_manual_lock_until.get(z)
                if lock and lock > now:
                    continue
                next_zone = z
                break

        if not next_zone:
            return 0

        zone_name = next_zone.split(".")[-1]
        lp = c.learned_power.get(zone_name, 1200)
        safety_mult = 1.15 if c.samples >= 10 else 1.30
        return round(lp * safety_mult)


class SolarACExportMarginSensor(_BaseSolarACSensor):
    @property
    def name(self):
        return "Solar AC Export Margin"

    @property
    def unique_id(self):
        return "solar_ac_export_margin"

    @property
    def state(self):
        c = self.coordinator
        required = SolarACRequiredExportSensor(c).state
        export = -c.ema_30s
        return round(export - required, 2)


class SolarACImportPowerSensor(_BaseSolarACSensor):
    @property
    def name(self):
        return "Solar AC Import Power"

    @property
    def unique_id(self):
        return "solar_ac_import_power"

    @property
    def state(self):
        return round(self.coordinator.ema_5m, 2)


class SolarACLearnedPowerSensor(_BaseSolarACSensor):
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
        return self.coordinator.learned_power.get(self.zone_name, 1200)
