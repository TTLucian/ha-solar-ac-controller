from __future__ import annotations

from typing import Any, Callable
from datetime import datetime

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
    """Set up sensors for the Solar AC Controller integration."""
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

    # Per-zone learned power sensors
    for zone in coordinator.config.get(CONF_ZONES, []):
        zone_name = zone.split(".")[-1]
        entities.append(SolarACLearnedPowerSensor(coordinator, entry_id, zone_name))

    # Diagnostics sensor is optional
    effective = {**entry.data, **entry.options}
    if effective.get(CONF_ENABLE_DIAGNOSTICS, False):
        entities.append(SolarACDiagnosticEntity(coordinator, entry_id))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# BASE CLASS
# ---------------------------------------------------------------------------

class _BaseSolarACSensor(SensorEntity):
    _attr_should_poll = False

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        self.coordinator = coordinator
        self._entry_id = entry_id
        # Use per-entry device identifier so all entities for this config entry
        # attach to the same device in Home Assistant.
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._entry_id)},
            "name": "Solar AC Controller",
            "configuration_url": "https://github.com/TTLucian/ha-solar-ac-controller",
        }
        self._unsub: Callable[[], None] | None = None

    @property
    def available(self) -> bool:
        """Prefer DataUpdateCoordinator last_update_success if available."""
        last_ok = getattr(self.coordinator, "last_update_success", None)
        if isinstance(last_ok, bool):
            return last_ok
        return True

    async def async_added_to_hass(self) -> None:
        try:
            self._unsub = self.coordinator.async_add_listener(self.async_write_ha_state)
        except Exception:
            # Fallback: write initial state if coordinator doesn't support listeners
            self.async_write_ha_state()

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
        return f"{self._entry_id}_active_zones"

    @property
    def state(self) -> str:
        zones = []
        for z in self.coordinator.config.get(CONF_ZONES, []):
            st = self.coordinator.hass.states.get(z)
            if st and st.state in ("heat", "cool", "on"):
                zones.append(z)
        return ", ".join(zones) if zones else "none"


class SolarACNextZoneSensor(_BaseSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Next Zone"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_next_zone"

    @property
    def state(self) -> str:
        return self.coordinator.next_zone or "none"


class SolarACLastZoneSensor(_BaseSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Last Zone"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_last_zone"

    @property
    def state(self) -> str:
        return self.coordinator.last_zone or "none"


class SolarACLastActionSensor(_BaseSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Last Action"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_last_action"

    @property
    def state(self) -> str:
        return self.coordinator.last_action or "none"


# ---------------------------------------------------------------------------
# NUMERIC SENSOR BASE CLASS
# ---------------------------------------------------------------------------

class _NumericSolarACSensor(_BaseSolarACSensor):
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
        return f"{self._entry_id}_ema_30s"

    @property
    def native_value(self) -> float:
        return round(getattr(self.coordinator, "ema_30s", 0.0), 2)


class SolarACEma5Sensor(_NumericSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC EMA 5m"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_ema_5m"

    @property
    def native_value(self) -> float:
        return round(getattr(self.coordinator, "ema_5m", 0.0), 2)


class SolarACConfidenceSensor(_BaseSolarACSensor):
    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "pts"

    @property
    def name(self) -> str:
        return "Solar AC Confidence"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_confidence"

    @property
    def native_value(self) -> float:
        return round(getattr(self.coordinator, "confidence", 0.0), 2)


class SolarACConfidenceThresholdSensor(_BaseSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Confidence Thresholds"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_conf_thresholds"

    @property
    def state(self) -> str:
        return "ok"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "add_threshold": getattr(self.coordinator, "add_confidence_threshold", None),
            "remove_threshold": getattr(self.coordinator, "remove_confidence_threshold", None),
        }


class SolarACRequiredExportSensor(_NumericSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Required Export"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_required_export"

    @property
    def native_value(self) -> float | None:
        val = getattr(self.coordinator, "required_export", None)
        return None if val is None else round(val, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # Explain source to the user: required_export is the learned power estimate
        return {
            "source": "learned_power",
            "note": "No safety multiplier applied; required_export equals learned power estimate.",
            "samples": getattr(self.coordinator, "samples", None),
        }


class SolarACExportMarginSensor(_NumericSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Export Margin"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_export_margin"

    @property
    def native_value(self) -> float | None:
        val = getattr(self.coordinator, "export_margin", None)
        return None if val is None else round(val, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "meaning": "export_margin = current_export - required_export",
            "positive_meaning": "positive = surplus available to add a zone",
            "required_export_source": "learned_power",
        }


class SolarACImportPowerSensor(_NumericSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Import Power"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_import_power"

    @property
    def native_value(self) -> float:
        return round(getattr(self.coordinator, "ema_5m", 0.0), 2)


# ---------------------------------------------------------------------------
# PANIC COOLDOWN SENSOR
# ---------------------------------------------------------------------------

class SolarACPanicCooldownSensor(_BaseSolarACSensor):
    @property
    def name(self) -> str:
        return "Solar AC Panic Cooldown Active"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_panic_cooldown"

    @property
    def state(self) -> str:
        ts = getattr(self.coordinator, "last_panic_ts", None)
        if not ts:
            return "no"
        cooldown = getattr(self.coordinator, "panic_cooldown_seconds", None) or getattr(self.coordinator, "_PANIC_COOLDOWN_SECONDS", 120)
        try:
            now = dt_util.utcnow().timestamp()
            return "yes" if (now - float(ts)) < float(cooldown) else "no"
        except Exception:
            return "no"


# ---------------------------------------------------------------------------
# LEARNED POWER SENSOR
# ---------------------------------------------------------------------------

class SolarACLearnedPowerSensor(_NumericSolarACSensor):
    def __init__(self, coordinator: Any, entry_id: str, zone_name: str):
        super().__init__(coordinator, entry_id)
        self.zone_name = zone_name

    @property
    def name(self) -> str:
        return f"Solar AC Learned Power {self.zone_name}"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_learned_power_{self.zone_name}"

    @property
    def native_value(self) -> float:
        return self.coordinator.get_learned_power(self.zone_name, mode="default")


# ---------------------------------------------------------------------------
# DIAGNOSTICS SENSOR (delegates to helpers.build_diagnostics)
# ---------------------------------------------------------------------------

class SolarACDiagnosticEntity(_BaseSolarACSensor):
    def __init__(self, coordinator: Any, entry_id: str):
        super().__init__(coordinator, entry_id)
        self._attr_name = "Solar AC Diagnostics"
        self._attr_icon = "mdi:brain"
        self._attr_unique_id = f"{self._entry_id}_diagnostics"

    @property
    def native_value(self) -> str:
        return getattr(self.coordinator, "last_action", None) or "idle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        try:
            attrs = build_diagnostics(self.coordinator)
            return attrs if isinstance(attrs, dict) else {}
        except Exception:
            return {
                "last_action": getattr(self.coordinator, "last_action", None),
                "samples": getattr(self.coordinator, "samples", None),
            }
