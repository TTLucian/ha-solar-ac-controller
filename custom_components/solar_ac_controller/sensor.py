from __future__ import annotations

import asyncio
from functools import cached_property
from typing import Any, Callable, cast

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ENABLE_DIAGNOSTICS_SENSOR,
    CONF_ZONES,
    DECISION_IMPORT_TOLERANCE_MAX_W,
    DOMAIN,
    SolarACData,
)
from .helpers import build_diagnostics


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain_data: SolarACData = hass.data[DOMAIN]
    data = domain_data[entry.entry_id]
    coordinator = data["coordinator"]
    entry_id = entry.entry_id

    entities: list[SensorEntity] = [
        SolarACActiveZonesSensor(coordinator, entry_id),
        SolarACActiveZoneCountSensor(coordinator, entry_id),
        SolarACNextZoneSensor(coordinator, entry_id),
        SolarACLastZoneSensor(coordinator, entry_id),
        SolarACLastActionSensor(coordinator, entry_id),
        SolarACEma30Sensor(coordinator, entry_id),
        SolarACEma5Sensor(coordinator, entry_id),
        SolarACConfidenceSensor(coordinator, entry_id),
        SolarACConfidenceThresholdSensor(coordinator, entry_id),
        SolarACRequiredExportSensor(coordinator, entry_id),
        SolarACRequiredExportSourceSensor(coordinator, entry_id),
        SolarACExportMarginSensor(coordinator, entry_id),
        SolarACLearnedIdlePowerSensor(coordinator, entry_id),
        SolarACCompressorRecoverySensor(coordinator, entry_id),
        SolarACGridImportToleranceSensor(coordinator, entry_id),
        SolarACSamplesSensor(coordinator, entry_id),
        SolarACLastRelearn(coordinator, entry_id),
    ]

    # Add per-decision breakdown diagnostic sensors when diagnostics enabled
    if entry.options.get(
        CONF_ENABLE_DIAGNOSTICS_SENSOR,
        entry.data.get(CONF_ENABLE_DIAGNOSTICS_SENSOR, False),
    ):
        entities.append(SolarACAddBreakdownSensor(coordinator, entry_id))
        entities.append(SolarACRemoveBreakdownSensor(coordinator, entry_id))
        entities.append(SolarACSolarSlopeSensor(coordinator, entry_id))
        entities.append(SolarACSolarFractionSensor(coordinator, entry_id))
        for zone in coordinator.config.get(CONF_ZONES, []):
            zone_name = zone.split(".")[-1]
            entities.append(
                SolarACZonePeakDeltaSensor(coordinator, entry_id, zone_name)
            )

    for zone in coordinator.config.get(CONF_ZONES, []):
        zone_name = zone.split(".")[-1]
        entities.append(SolarACLearnedPowerSensor(coordinator, entry_id, zone_name))
        entities.append(
            SolarACZoneLockRemainingSensor(coordinator, entry_id, zone_name, zone)
        )

    if entry.options.get(
        CONF_ENABLE_DIAGNOSTICS_SENSOR,
        entry.data.get(CONF_ENABLE_DIAGNOSTICS_SENSOR, False),
    ):
        entities.append(SolarACDiagnosticEntity(coordinator, entry_id))

    async_add_entities(entities)


# --- BASE CLASS ---
class _BaseSolarACSensor(SensorEntity):
    """
    Base class for all Solar AC Controller sensors.
    Handles coordinator listener and device info.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        self.coordinator: Any = coordinator
        self._entry_id: str = entry_id
        self._unsub: Callable[[], None] | None = None
        self._previous_state: Any = None
        self._previous_attributes: dict[str, Any] = {}
        # Prevent overlapping async write tasks
        self._write_lock: asyncio.Lock = asyncio.Lock()

    @cached_property
    def device_info(self) -> DeviceInfo:
        """Link to the 'Solar AC Controller' device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Solar AC Controller",
        )

    def _state_changed(self) -> bool:
        """Check if the entity state or attributes have changed."""
        current_state = self.state
        current_attributes = self.extra_state_attributes or {}

        # Compare state first (fast path)
        if current_state != self._previous_state:
            return True

        # Compare full attributes — sensors like breakdown sensors have a static
        # state of "ok" but carry all their data in extra_state_attributes, so
        # we must check the whole dict (not just a fixed subset of metadata keys).
        if current_attributes != self._previous_attributes:
            return True

        return False

    async def _smart_write_ha_state(self) -> None:
        """Write state to HA only if it has actually changed."""
        async with self._write_lock:
            if self._state_changed():
                self._previous_state = self.state
                self._previous_attributes = dict(self.extra_state_attributes or {})
                self.async_write_ha_state()

    def _sync_write_ha_state(self) -> None:
        """Synchronous wrapper to schedule async state update."""
        # Prefer Home Assistant's task creation when available
        if getattr(self, "hass", None):
            # Use coordinator's safe task creator when available
            if getattr(self, "coordinator", None) and hasattr(
                self.coordinator, "create_background_task"
            ):
                self.coordinator.create_background_task(self._smart_write_ha_state())
            else:
                # Fallback: use coordinator's create_task or hass.async_create_task
                try:
                    if getattr(self, "coordinator", None) and hasattr(
                        self.coordinator, "create_task"
                    ):
                        self.coordinator.create_task(self._smart_write_ha_state())
                    else:
                        self.hass.async_create_task(self._smart_write_ha_state())
                except Exception:
                    try:
                        self.hass.async_create_task(self._smart_write_ha_state())
                    except Exception:
                        pass
        else:
            asyncio.create_task(self._smart_write_ha_state())

    async def async_added_to_hass(self) -> None:
        """Register listener for coordinator updates."""
        try:
            self._unsub = self.coordinator.async_add_listener(self._sync_write_ha_state)
        except (AttributeError, TypeError):
            await self._smart_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Remove coordinator listener on entity removal."""
        if self._unsub:
            self._unsub()


# --- SENSOR CLASSES ---
class SolarACActiveZonesSensor(_BaseSolarACSensor):
    _attr_name = "Active Zones"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_active_zones"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> str:
        zones = [
            z
            for z in self.coordinator.config.get(CONF_ZONES, [])
            if (st := self.coordinator.hass.states.get(z))
            and st.state in ("heat", "cool", "on")
        ]
        return ", ".join(zones) if zones else "none"


class _ZoneStateSensor(_BaseSolarACSensor):
    zone_attr = ""

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> str:
        return getattr(self.coordinator, self.zone_attr, "none")


class SolarACNextZoneSensor(_ZoneStateSensor):
    _attr_name = "Next Zone"
    zone_attr = "next_zone"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_next_zone"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> str:
        return self.coordinator.next_zone or "none"


class SolarACLastZoneSensor(_ZoneStateSensor):
    _attr_name = "Last Zone"
    zone_attr = "last_zone"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_last_zone"


class SolarACLastActionSensor(_BaseSolarACSensor):
    _attr_name = "Last Action"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_last_action"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> str:
        return self.coordinator.last_action or "none"


class _NumericSolarACSensor(_BaseSolarACSensor):
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "W"


class SolarACEma30Sensor(_NumericSolarACSensor):
    _attr_name = "EMA 30s"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_ema_30s"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> float:
        return round(getattr(self.coordinator, "ema_30s", 0.0), 2)


class SolarACEma5Sensor(_NumericSolarACSensor):
    _attr_name = "EMA 5m"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_ema_5m"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> float:
        return round(getattr(self.coordinator, "ema_5m", 0.0), 2)


class SolarACConfidenceSensor(_BaseSolarACSensor):
    _attr_name = "Confidence"
    _attr_native_unit_of_measurement = "pts"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_confidence"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> float:
        return round(getattr(self.coordinator, "confidence", 0.0), 2)


class SolarACConfidenceThresholdSensor(_BaseSolarACSensor):
    _attr_name = "Confidence Thresholds"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_conf_thresholds"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> str:
        return "ok"

    @property  # pyright: ignore[reportIncompatibleVariableOverride]
    def extra_state_attributes(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> dict:
        return {
            "add_threshold": getattr(self.coordinator, "unified_add_threshold", None),
            "remove_threshold": getattr(
                self.coordinator, "unified_remove_threshold", None
            ),
        }


class SolarACRequiredExportSensor(_NumericSolarACSensor):
    _attr_name = "Required Export"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_required_export"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> float | None:
        val = getattr(self.coordinator, "required_export", None)
        return round(val, 2) if val is not None else None


class SolarACExportMarginSensor(_NumericSolarACSensor):
    _attr_name = "Export Margin"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_export_margin"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> float | None:
        val = getattr(self.coordinator, "export_margin", None)
        return round(val, 2) if val is not None else None


class SolarACSamplesSensor(_BaseSolarACSensor):
    _attr_name = "Samples"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_samples"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> int:
        return getattr(self.coordinator, "samples", 0)


class SolarACLastRelearn(_BaseSolarACSensor):
    """Timestamp of the most recent force_relearn service call.

    The ``target`` extra attribute contains the zone that was reset
    (``"all"`` when every zone was cleared).
    """

    _attr_name = "Last Relearn"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:brain-freeze-outline"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_last_relearn"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> Any:
        return getattr(self.coordinator, "last_relearn_at", None)

    @property  # pyright: ignore[reportIncompatibleVariableOverride]
    def extra_state_attributes(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> dict[str, object]:
        target = getattr(self.coordinator, "last_relearn_target", "")
        return {"target": target} if target else {}


class SolarACLearnedPowerSensor(_NumericSolarACSensor):
    def __init__(self, coordinator: Any, entry_id: str, zone_name: str):
        super().__init__(coordinator, entry_id)
        self.zone_name = zone_name
        self._attr_name = f"Learned Power {zone_name}"
        self._attr_unique_id = f"{self._entry_id}_learned_power_{zone_name}"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> float:
        return cast(
            float,
            self.coordinator.get_learned_power(
                self.zone_name, self.coordinator.season_mode
            ),
        )


class SolarACAddBreakdownSensor(_BaseSolarACSensor):
    _attr_name = "Add Confidence Breakdown"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_add_conf_breakdown"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> str:
        return "ok"

    @property  # pyright: ignore[reportIncompatibleVariableOverride]
    def extra_state_attributes(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> dict[str, object]:
        return getattr(self.coordinator, "last_add_breakdown", {}) or {}


class SolarACRemoveBreakdownSensor(_BaseSolarACSensor):
    _attr_name = "Remove Confidence Breakdown"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_remove_conf_breakdown"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> str:
        return "ok"

    @property  # pyright: ignore[reportIncompatibleVariableOverride]
    def extra_state_attributes(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> dict[str, object]:
        return getattr(self.coordinator, "last_remove_breakdown", {}) or {}


class SolarACSolarSlopeSensor(_NumericSolarACSensor):
    """Difference between solar fast EMA and solar slow EMA (W).

    A negative value means solar production is dropping (cloud approaching).
    A positive value means production is rising (cloud clearing or morning ramp).
    Used internally to distinguish cloud shadows from household load spikes.
    """

    _attr_name = "Solar Slope"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_solar_slope"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> float:
        fast = getattr(self.coordinator, "solar_ema_fast", 0.0)
        slow = getattr(self.coordinator, "solar_ema_slow", 0.0)
        return round(fast - slow, 2)


class SolarACSolarFractionSensor(_BaseSolarACSensor):
    """Solar production as a fraction (0.0–1.0) of rated PV capacity.

    Only meaningful when `pv_capacity_w` is configured in options.
    Returns 0.0 when the capacity is not set.
    """

    _attr_name = "Solar Fraction"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:solar-power"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_solar_fraction"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> float:
        fraction = getattr(self.coordinator, "solar_fraction", 0.0)
        return round(fraction * 100.0, 1)


class SolarACDiagnosticEntity(_BaseSolarACSensor):
    _attr_name = "Diagnostics"
    _attr_icon = "mdi:brain"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_diagnostics"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> str:
        """Show meaningful state changes for logbook when activity logging is enabled, avoid noise from fluctuating values."""
        # If integration is disabled, don't log to avoid logbook entries
        if not getattr(self.coordinator, "integration_enabled", True):
            return "integration_disabled_quiet"

        # If activity logging is disabled, don't change state to avoid logbook spam
        if not getattr(self.coordinator, "activity_logging_enabled", False):
            return "activity_logging_disabled"

        last_action = getattr(self.coordinator, "last_action", "idle") or "idle"
        note = getattr(self.coordinator, "note", "") or ""

        # For stable states like "solar_too_low", don't include fluctuating details in state
        # This prevents excessive logbook entries from minor solar power variations
        stable_states = {
            "solar_too_low",
            "idle",
            "balanced",
            "integration_disabled_quiet",
        }

        if last_action in stable_states:
            return last_action
        elif last_action.startswith("add_") and note:
            return (
                f"{last_action}: {note.split(':')[1].strip() if ':' in note else note}"
            )
        elif last_action.startswith("remove_") and note:
            return (
                f"{last_action}: {note.split(':')[1].strip() if ':' in note else note}"
            )
        elif note:
            return f"{last_action}: {note}"

        return last_action

    @property  # pyright: ignore[reportIncompatibleVariableOverride]
    def extra_state_attributes(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> dict[str, object]:
        """
        Expose a JSON snapshot of the controller's internal state for diagnostics.
        Includes error field if diagnostics collection fails.
        """
        try:
            return build_diagnostics(self.coordinator)
        except (AttributeError, TypeError, ValueError, KeyError) as exc:
            return {"diagnostics_error": str(exc)}


# ---------------------------------------------------------------------------
# NEW SENSORS
# ---------------------------------------------------------------------------


class SolarACActiveZoneCountSensor(_BaseSolarACSensor):
    """Number of currently active (running) zones — useful for automations."""

    _attr_name = "Active Zone Count"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "zones"
    _attr_icon = "mdi:counter"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_active_zone_count"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> int:
        return sum(
            1
            for z in self.coordinator.config.get(CONF_ZONES, [])
            if (st := self.coordinator.hass.states.get(z))
            and st.state in ("heat", "cool", "on")
        )


class SolarACLearnedIdlePowerSensor(_NumericSolarACSensor):
    """Learned idle draw of the AC compressor while running but no zones active.

    Used by the master-switch spindown guard — lower than this means the
    compressor has wound down and the relay can be safely cut.
    """

    _attr_name = "Learned Idle Power"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_learned_idle_power"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> float:
        return round(getattr(self.coordinator, "learned_idle_power", 0.0), 1)


class SolarACRequiredExportSourceSensor(_BaseSolarACSensor):
    """Reason code for why required_export has its current value (ENUM sensor).

    Possible state keys: learned_power, manual_power_override, panic_recovery,
    integration_disabled, solar_freeze, initializing.
    """

    _attr_name = "Required Export Source"
    _attr_icon = "mdi:information-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "required_export_source"
    _attr_options = [
        "learned_power",
        "manual_power_override",
        "panic_recovery",
        "integration_disabled",
        "solar_freeze",
        "initializing",
    ]

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_required_export_source"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> str:
        return (
            getattr(self.coordinator, "required_export_source", "initializing")
            or "initializing"
        )


class SolarACCompressorRecoverySensor(_BaseSolarACSensor):
    """Seconds remaining on the compressor recovery guard (0 = clear to add zones).

    After a zone is added and the compressor ramps up, this counts down from
    compressor_ramp_seconds to zero, suppressing further zone additions with a
    decaying confidence penalty during that window.
    """

    _attr_name = "Compressor Recovery Remaining"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "s"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:timer-sand"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_compressor_recovery"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> float:
        recover_until = (
            getattr(self.coordinator, "compressor_recover_until", 0.0) or 0.0
        )
        remaining = recover_until - dt_util.utcnow().timestamp()
        return float(round(max(0.0, remaining), 1))


class SolarACGridImportToleranceSensor(_NumericSolarACSensor):
    """Current grid import tolerance derived from the Aggressiveness slider.

    Computed as: aggressiveness × 700 W.  Shows how many watts of grid import
    the controller will accept while still allowing a zone to be added.
    Updates live whenever the aggressiveness number entity is changed.
    """

    _attr_name = "Grid Import Tolerance"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:transmission-tower-import"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_grid_import_tolerance"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> float:
        a = float(getattr(self.coordinator, "aggressiveness", 0.5))
        return round(a * DECISION_IMPORT_TOLERANCE_MAX_W, 1)


class SolarACZoneLockRemainingSensor(_BaseSolarACSensor):
    """Seconds until a zone's manual-override lock expires (0 = unlocked).

    When a zone is manually turned on/off outside of integration control the
    system locks it for manual_lock_seconds to avoid fighting the user.
    """

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "s"
    _attr_icon = "mdi:lock-clock"

    def __init__(
        self, coordinator: Any, entry_id: str, zone_name: str, zone_id: str
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._zone_name = zone_name
        self._zone_id = zone_id
        self._attr_name = f"Zone Lock Remaining {zone_name}"
        self._attr_unique_id = f"{entry_id}_zone_lock_remaining_{zone_name}"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> float:
        now = dt_util.utcnow().timestamp()
        # Short-cycle protection remaining (mirrors is_short_cycling logic)
        last = self.coordinator.zone_last_changed.get(self._zone_id)
        if last:
            last_type = self.coordinator.zone_last_changed_type.get(self._zone_id)
            if last_type == "on":
                threshold = self.coordinator.short_cycle_on_seconds
            else:
                threshold = self.coordinator.short_cycle_off_seconds
            short_cycle_remaining = max(0.0, (last + float(threshold)) - now)
        else:
            short_cycle_remaining = 0.0
        # Manual lock remaining (explicit lock-until timestamp)
        until = self.coordinator.zone_manual_lock_until.get(self._zone_id, 0.0) or 0.0
        manual_remaining = max(0.0, until - now)
        return float(round(max(short_cycle_remaining, manual_remaining), 0))


class SolarACZonePeakDeltaSensor(_NumericSolarACSensor):
    """Learned compressor startup surge for a zone (peak delta, in watts).

    This is the power spike measured during the first ~60 s after a zone is
    added.  The add-decision uses this value (not steady-state learned power)
    as the required_export bar once it has been measured.
    Only shown when diagnostics sensor is enabled.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: Any, entry_id: str, zone_name: str) -> None:
        super().__init__(coordinator, entry_id)
        self._zone_name = zone_name
        self._attr_name = f"Peak Delta {zone_name}"
        self._attr_unique_id = f"{entry_id}_peak_delta_{zone_name}"

    @property
    def native_value(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> float | None:
        val = self.coordinator.get_peak_delta(
            self._zone_name,
            mode=getattr(self.coordinator, "season_mode", None),
        )
        return round(val, 0) if val is not None else None
