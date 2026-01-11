from __future__ import annotations

from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, CONF_ENABLE_DIAGNOSTICS, CONF_ZONES
from .helpers import build_diagnostics


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities: list[SensorEntity] = [
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
    for zone in coordinator.config.get(CONF_ZONES, []):
        zone_name = zone.split(".")[-1]
        entities.append(SolarACLearnedPowerSensor(coordinator, zone_name))

    # Diagnostics sensor (optional, behind toggle)
    effective = {**entry.data, **entry.options}
    if effective.get(CONF_ENABLE_DIAGNOSTICS, False):
        entities.append(SolarACDiagnosticEntity(coordinator))

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
        self._unsub: Callable[[], None] | None = None

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        self._unsub = self.coordinator.async_add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        if callable(self._unsub):
            try:
                self._unsub()
            except Exception:
                pass
            finally:
                self._unsub = None


# ---------------------------------------------------------------------------
# NON-NUMERIC SENSORS
# ---------------------------------------------------------------------------

class SolarACActiveZonesSensor(_BaseSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Active Zones"

    @property
    def unique_id(self) -> str:
        return "solar_ac_active_zones"

    @property
    def state(self) -> str:
        zones = []
        for z in self.coordinator.config.get(CONF_ZONES, []):
            st = self.coordinator.hass.states.get(z)
            # Treat heating, cooling and generic "on" as active
            if st and st.state in ("heat", "cool", "on"):
                zones.append(z)
        return ", ".join(zones) if zones else "none"


class SolarACNextZoneSensor(_BaseSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Next Zone"

    @property
    def unique_id(self) -> str:
        return "solar_ac_next_zone"

    @property
    def state(self) -> str:
        return self.coordinator.next_zone or "none"


class SolarACLastZoneSensor(_BaseSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Last Zone"

    @property
    def unique_id(self) -> str:
        return "solar_ac_last_zone"

    @property
    def state(self) -> str:
        return self.coordinator.last_zone or "none"


class SolarACLastActionSensor(_BaseSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Last Action"

    @property
    def unique_id(self) -> str:
        return "solar_ac_last_action"

    @property
    def state(self) -> str:
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
    def name(self) -> str:
        return "Solar AC EMA 30s"

    @property
    def unique_id(self) -> str:
        return "solar_ac_ema_30s"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.ema_30s, 2)


class SolarACEma5Sensor(_NumericSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC EMA 5m"

    @property
    def unique_id(self) -> str:
        return "solar_ac_ema_5m"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.ema_5m, 2)


class SolarACConfidenceSensor(_BaseSolarACSensor):
    """Dimensionless numeric confidence value."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "pts"
    _attr_device_class = None

    @property
    def name(self) -> str:
        return "Solar AC Confidence"

    @property
    def unique_id(self) -> str:
        return "solar_ac_confidence"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.confidence, 2)


class SolarACConfidenceThresholdSensor(_BaseSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Confidence Thresholds"

    @property
    def unique_id(self) -> str:
        return "solar_ac_conf_thresholds"

    @property
    def state(self) -> str:
        return "ok"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "add_threshold": self.coordinator.add_confidence_threshold,
            "remove_threshold": self.coordinator.remove_confidence_threshold,
        }


class SolarACRequiredExportSensor(_NumericSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Required Export"

    @property
    def unique_id(self) -> str:
        return "solar_ac_required_export"

    @property
    def native_value(self) -> float | None:
        val = self.coordinator.required_export
        return None if val is None else round(val, 2)


class SolarACExportMarginSensor(_NumericSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Export Margin"

    @property
    def unique_id(self) -> str:
        return "solar_ac_export_margin"

    @property
    def native_value(self) -> float | None:
        val = self.coordinator.export_margin
        return None if val is None else round(val, 2)


class SolarACImportPowerSensor(_NumericSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Import Power"

    @property
    def unique_id(self) -> str:
        return "solar_ac_import_power"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.ema_5m, 2)


# ---------------------------------------------------------------------------
# TIMESTAMP SENSORS
# ---------------------------------------------------------------------------

class SolarACMasterOffSinceSensor(_BaseSolarACSensor):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def name(self) -> str:
        return "Solar AC Master Off Since"

    @property
    def unique_id(self) -> str:
        return "solar_ac_master_off_since"

    @property
    def native_value(self) -> str | None:
        ts = self.coordinator.master_off_since
        if not ts:
            return None
        # Return localized ISO 8601 timestamp (Home Assistant timezone)
        return dt_util.as_local(dt_util.utc_from_timestamp(ts)).isoformat()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ts = self.coordinator.master_off_since
        return {"utc_iso": dt_util.utc_from_timestamp(ts).isoformat() if ts else None}


class SolarACLastPanicSensor(_BaseSolarACSensor):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def name(self) -> str:
        return "Solar AC Last Panic"

    @property
    def unique_id(self) -> str:
        return "solar_ac_last_panic"

    @property
    def native_value(self) -> str | None:
        ts = self.coordinator.last_panic_ts
        if not ts:
            return None
        return dt_util.as_local(dt_util.utc_from_timestamp(ts)).isoformat()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ts = self.coordinator.last_panic_ts
        return {"utc_iso": dt_util.utc_from_timestamp(ts).isoformat() if ts else None}


class SolarACPanicCooldownSensor(_BaseSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Panic Cooldown Active"

    @property
    def unique_id(self) -> str:
        return "solar_ac_panic_cooldown"

    @property
    def state(self) -> str:
        now = dt_util.utcnow().timestamp()
        ts = self.coordinator.last_panic_ts
        if not ts:
            return "no"
        return "yes" if (now - ts) < 120 else "no"


# ---------------------------------------------------------------------------
# LEARNED POWER SENSOR
# ---------------------------------------------------------------------------

class SolarACLearnedPowerSensor(_NumericSolarACSensor):
    def __init__(self, coordinator, zone_name: str):
        super().__init__(coordinator)
        self.zone_name = zone_name

    @property
    def name(self) -> str:
        return f"Solar AC Learned Power {self.zone_name}"

    @property
    def unique_id(self) -> str:
        return f"solar_ac_learned_power_{self.zone_name}"

    @property
    def native_value(self) -> float:
        # Use coordinator accessor to remain compatible with legacy and per-mode storage.
        return self.coordinator.get_learned_power(self.zone_name, mode="default")


# ---------------------------------------------------------------------------
# DIAGNOSTICS SENSOR
# ---------------------------------------------------------------------------

class SolarACDiagnosticEntity(_BaseSolarACSensor):
    """A single sensor exposing the entire controller state as JSON attributes."""

    _attr_should_poll = False
    _attr_name = "Solar AC Diagnostics"
    _attr_icon = "mdi:brain"
    # Omit non-standard device_class

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = "solar_ac_diagnostics"

    @property
    def native_value(self) -> str:
        return self.coordinator.last_action or "idle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return build_diagnostics(self.coordinator)
