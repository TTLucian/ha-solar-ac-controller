from __future__ import annotations

from typing import Any, Callable
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_ENABLE_DIAGNOSTICS, CONF_ZONES
from .helpers import build_diagnostics


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
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

    effective = {**entry.data, **entry.options}
    if effective.get(CONF_ENABLE_DIAGNOSTICS, False):
        entities.append(SolarACDiagnosticEntity(coordinator, entry_id))

    async_add_entities(entities)


class _BaseSolarACSensor(SensorEntity):
    """Base sensor WITHOUT device_info (per user request)."""

    _attr_should_poll = False

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        self.coordinator = coordinator
        self._entry_id = entry_id
        self._unsub: Callable[[], None] | None = None

    @property
    def available(self) -> bool:
        last_ok = getattr(self.coordinator, "last_update_success", None)
        return bool(last_ok) if isinstance(last_ok, bool) else True

    async def async_added_to_hass(self) -> None:
        add_listener = getattr(self.coordinator, "async_add_listener", None)
        if callable(add_listener):
            try:
                self._unsub = add_listener(self.async_write_ha_state)
            except Exception:
                self.async_write_ha_state()
        else:
            self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if callable(self._unsub):
            try:
                self._unsub()
            except Exception:
                pass
            finally:
                self._unsub = None


class _NumericSolarACSensor(_BaseSolarACSensor):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "W"


# Concrete sensor implementations

class SolarACActiveZonesSensor(_BaseSolarACSensor):
    _attr_name = "Solar AC Active Zones"
    _attr_unique_id = "active_zones"

    @property
    def native_value(self):
        return getattr(self.coordinator, "active_zones", None)


class SolarACNextZoneSensor(_BaseSolarACSensor):
    _attr_name = "Solar AC Next Zone"
    _attr_unique_id = "next_zone"

    @property
    def native_value(self):
        return getattr(self.coordinator, "next_zone", None)


class SolarACLastZoneSensor(_BaseSolarACSensor):
    _attr_name = "Solar AC Last Zone"
    _attr_unique_id = "last_zone"

    @property
    def native_value(self):
        return getattr(self.coordinator, "last_zone", None)


class SolarACLastActionSensor(_BaseSolarACSensor):
    _attr_name = "Solar AC Last Action"
    _attr_unique_id = "last_action"

    @property
    def native_value(self):
        return getattr(self.coordinator, "last_action", None) or "idle"


class SolarACEma30Sensor(_NumericSolarACSensor):
    _attr_name = "Solar AC EMA 30s"
    _attr_unique_id = "ema_30s"

    @property
    def native_value(self):
        return getattr(self.coordinator, "ema_30s", None)


class SolarACEma5Sensor(_NumericSolarACSensor):
    _attr_name = "Solar AC EMA 5m"
    _attr_unique_id = "ema_5m"

    @property
    def native_value(self):
        return getattr(self.coordinator, "ema_5m", None)


class SolarACConfidenceSensor(_NumericSolarACSensor):
    _attr_name = "Solar AC Confidence"
    _attr_unique_id = "confidence"

    @property
    def native_value(self):
        return getattr(self.coordinator, "confidence", None)


class SolarACConfidenceThresholdSensor(_NumericSolarACSensor):
    _attr_name = "Solar AC Confidence Threshold"
    _attr_unique_id = "confidence_threshold"

    @property
    def native_value(self):
        return getattr(self.coordinator, "confidence_threshold", None)


class SolarACRequiredExportSensor(_NumericSolarACSensor):
    _attr_name = "Solar AC Required Export"
    _attr_unique_id = "required_export"

    @property
    def native_value(self):
        return getattr(self.coordinator, "required_export", None)


class SolarACExportMarginSensor(_NumericSolarACSensor):
    _attr_name = "Solar AC Export Margin"
    _attr_unique_id = "export_margin"

    @property
    def native_value(self):
        return getattr(self.coordinator, "export_margin", None)


class SolarACImportPowerSensor(_NumericSolarACSensor):
    _attr_name = "Solar AC Import Power"
    _attr_unique_id = "import_power"

    @property
    def native_value(self):
        return getattr(self.coordinator, "import_power", None)


class SolarACPanicCooldownSensor(_BaseSolarACSensor):
    _attr_name = "Solar AC Panic Cooldown Active"
    _attr_unique_id = "panic_cooldown_active"

    @property
    def native_value(self):
        return getattr(self.coordinator, "panic_cooldown_active", False)


class SolarACLearnedPowerSensor(_NumericSolarACSensor):
    def __init__(self, coordinator: Any, entry_id: str, zone: str) -> None:
        super().__init__(coordinator, entry_id)
        self.zone = zone
        self._attr_name = f"Solar AC Learned Power {zone}"
        self._attr_unique_id = f"learned_power_{zone}"

    @property
    def native_value(self):
        learned = getattr(self.coordinator, "learned_power", {}) or {}
        # learned_power structure may be {zone: {"default":..,"heat":..,"cool":..}}
        zone_val = learned.get(self.zone)
        if isinstance(zone_val, dict):
            # return default estimate
            return zone_val.get("default")
        return zone_val


class SolarACDiagnosticEntity(_BaseSolarACSensor):
    _attr_name = "Solar AC Diagnostics"
    _attr_unique_id = "diagnostics"

    @property
    def extra_state_attributes(self):
        try:
            return build_diagnostics(self.coordinator)
        except Exception:
            return {
                "last_action": getattr(self.coordinator, "last_action", None),
                "samples": getattr(self.coordinator, "samples", None),
            }
