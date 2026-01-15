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
from homeassistant.helpers.entity import DeviceInfo
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
    entry_id = entry.entry_id

    entities: list[SensorEntity] = [
        SolarACActiveZonesSensor(coordinator, entry_id),
        SolarACNextZoneSensor(coordinator, entry_id),
        SolarACLastZoneSensor(coordinator, entry_id),
        SolarACLastActionSensor(coordinator, entry_id),
        SolarACEma30Sensor(coordinator, entry_id),
        SolarACEma5Sensor(coordinator, entry_id),
        SolarACConfidenceSensor(coordinator, entry_id),
        SolarACConfidenceThresholdSensor(coordinator, entry_id),
        SolarACRequiredExportSensor(coordinator, entry_id),
        SolarACExportMarginSensor(coordinator, entry_id),
        SolarACImportPowerSensor(coordinator, entry_id),
        SolarACPanicCooldownSensor(coordinator, entry_id),
    ]

    for zone in coordinator.config.get(CONF_ZONES, []):
        zone_name = zone.split(".")[-1]
        entities.append(SolarACLearnedPowerSensor(coordinator, entry_id, zone_name))

    if entry.options.get(CONF_ENABLE_DIAGNOSTICS, False):
        entities.append(SolarACDiagnosticEntity(coordinator, entry_id))

    async_add_entities(entities)

# --- BASE CLASS ---
class _BaseSolarACSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        self.coordinator = coordinator
        self._entry_id = entry_id
        self._unsub: Callable[[], None] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Link to the 'Solar AC Controller' device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Solar AC Controller",
            model="Solar AC Logic Controller",
            sw_version=getattr(self.coordinator, "version", "0.5.1"),
        )

    async def async_added_to_hass(self) -> None:
        try:
            self._unsub = self.coordinator.async_add_listener(self.async_write_ha_state)
        except Exception:
            self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()

# --- SENSOR CLASSES ---
class SolarACActiveZonesSensor(_BaseSolarACSensor):
    _attr_name = "Active Zones"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_active_zones"
    @property
    def state(self) -> str:
        zones = [z for z in self.coordinator.config.get(CONF_ZONES, []) 
                 if (st := self.coordinator.hass.states.get(z)) and st.state in ("heat", "cool", "on")]
        return ", ".join(zones) if zones else "none"

class SolarACNextZoneSensor(_BaseSolarACSensor):
    _attr_name = "Next Zone"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_next_zone"
    @property
    def state(self) -> str: return self.coordinator.next_zone or "none"

class SolarACLastZoneSensor(_BaseSolarACSensor):
    _attr_name = "Last Zone"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_last_zone"
    @property
    def state(self) -> str: return self.coordinator.last_zone or "none"

class SolarACLastActionSensor(_BaseSolarACSensor):
    _attr_name = "Last Action"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_last_action"
    @property
    def state(self) -> str: return self.coordinator.last_action or "none"

class _NumericSolarACSensor(_BaseSolarACSensor):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "W"

class SolarACEma30Sensor(_NumericSolarACSensor):
    _attr_name = "EMA 30s"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_ema_30s"
    @property
    def native_value(self) -> float: return round(getattr(self.coordinator, "ema_30s", 0.0), 2)

class SolarACEma5Sensor(_NumericSolarACSensor):
    _attr_name = "EMA 5m"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_ema_5m"
    @property
    def native_value(self) -> float: return round(getattr(self.coordinator, "ema_5m", 0.0), 2)

class SolarACConfidenceSensor(_BaseSolarACSensor):
    _attr_name = "Confidence"
    _attr_native_unit_of_measurement = "pts"
    _attr_state_class = SensorStateClass.MEASUREMENT
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_confidence"
    @property
    def native_value(self) -> float: return round(getattr(self.coordinator, "confidence", 0.0), 2)

class SolarACConfidenceThresholdSensor(_BaseSolarACSensor):
    _attr_name = "Confidence Thresholds"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_conf_thresholds"
    @property
    def state(self) -> str: return "ok"
    @property
    def extra_state_attributes(self) -> dict:
        return {
            "add_threshold": getattr(self.coordinator, "add_confidence_threshold", None),
            "remove_threshold": getattr(self.coordinator, "remove_confidence_threshold", None),
        }

class SolarACRequiredExportSensor(_NumericSolarACSensor):
    _attr_name = "Required Export"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_required_export"
    @property
    def native_value(self) -> float | None:
        val = getattr(self.coordinator, "required_export", None)
        return round(val, 2) if val is not None else None

class SolarACExportMarginSensor(_NumericSolarACSensor):
    _attr_name = "Export Margin"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_export_margin"
    @property
    def native_value(self) -> float | None:
        val = getattr(self.coordinator, "export_margin", None)
        return round(val, 2) if val is not None else None

class SolarACImportPowerSensor(_NumericSolarACSensor):
    _attr_name = "Import Power"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_import_power"
    @property
    def native_value(self) -> float: return round(getattr(self.coordinator, "ema_5m", 0.0), 2)

class SolarACPanicCooldownSensor(_BaseSolarACSensor):
    _attr_name = "Panic Cooldown Active"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_panic_cooldown"
    @property
    def state(self) -> str:
        ts = getattr(self.coordinator, "last_panic_ts", None)
        if not ts: return "no"
        cooldown = getattr(self.coordinator, "panic_cooldown_seconds", 120)
        return "yes" if (dt_util.utcnow().timestamp() - float(ts)) < float(cooldown) else "no"

class SolarACLearnedPowerSensor(_NumericSolarACSensor):
    def __init__(self, coordinator: Any, entry_id: str, zone_name: str):
        super().__init__(coordinator, entry_id)
        self.zone_name = zone_name
        self._attr_name = f"Learned Power {zone_name}"
        self._attr_unique_id = f"{self._entry_id}_learned_power_{zone_name}"
    @property
    def native_value(self) -> float: return self.coordinator.get_learned_power(self.zone_name)

class SolarACDiagnosticEntity(_BaseSolarACSensor):
    _attr_name = "Diagnostics"
    _attr_icon = "mdi:brain"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_diagnostics"
    @property
    def native_value(self) -> str: return getattr(self.coordinator, "last_action", "idle")
    @property
    def extra_state_attributes(self) -> dict: return build_diagnostics(self.coordinator)
