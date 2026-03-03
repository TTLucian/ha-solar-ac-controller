from __future__ import annotations

import logging
from functools import cached_property
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_AC_SWITCH, CONF_ZONES, DOMAIN, SolarACData

if TYPE_CHECKING:
    from .coordinator import SolarACCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    domain_data: SolarACData = hass.data[DOMAIN]
    data = domain_data[entry.entry_id]
    coordinator = data["coordinator"]
    entry_id = entry.entry_id

    entities = [
        SolarACLearningBinarySensor(coordinator, entry_id),
        SolarACPanicBinarySensor(coordinator, entry_id),
        SolarACPanicCooldownBinarySensor(coordinator, entry_id),
        SolarACShortCycleBinarySensor(coordinator, entry_id),
        SolarACLockedBinarySensor(coordinator, entry_id),
        SolarACExportingBinarySensor(coordinator, entry_id),
        SolarACImportingBinarySensor(coordinator, entry_id),
        SolarACMasterBinarySensor(coordinator, entry_id),
    ]
    async_add_entities(entities)


# --- BASE CLASS ---
class _BaseSolarACBinary(  # pyright: ignore[reportIncompatibleVariableOverride]
    CoordinatorEntity, BinarySensorEntity
):
    coordinator: "SolarACCoordinator"
    """
    Base class for all Solar AC Controller binary sensors.
    Inherits from CoordinatorEntity for automatic listener management.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id: str = entry_id

    @cached_property
    def device_info(self) -> DeviceInfo:
        """Link to the 'Solar AC Controller' device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Solar AC Controller",
        )


# --- BINARY SENSORS ---
class SolarACLearningBinarySensor(_BaseSolarACBinary):
    _attr_name = "Learning Active"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_learning_active"

    @cached_property
    def is_on(self) -> bool:
        return bool(getattr(self.coordinator, "learning_active_cached", False))


class SolarACPanicBinarySensor(_BaseSolarACBinary):

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Panic State"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_panic_state"

    @cached_property
    def is_on(self) -> bool:
        return getattr(self.coordinator, "last_action", None) == "panic"


class SolarACPanicCooldownBinarySensor(_BaseSolarACBinary):

    # No device_class: this is a time-based state, not a direct problem
    _attr_name = "Panic Cooldown"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_panic_cooldown_bin"

    @cached_property
    def is_on(self) -> bool:
        # Note: This will only update when the coordinator updates.
        return self.coordinator.panic_manager.is_in_cooldown


class SolarACShortCycleBinarySensor(_BaseSolarACBinary):

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Short Cycling"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_short_cycling"

    @cached_property
    def is_on(self) -> bool:
        # Note: This will only update when the coordinator updates.
        now = dt_util.utcnow().timestamp()
        for z in self.coordinator.config.get(CONF_ZONES, []):
            if last := self.coordinator.zone_last_changed.get(z):
                threshold = (
                    self.coordinator.short_cycle_on_seconds
                    if self.coordinator.zone_last_changed_type.get(z) == "on"
                    else self.coordinator.short_cycle_off_seconds
                )
                if (now - last) < float(threshold):
                    return True
        return False


class SolarACLockedBinarySensor(_BaseSolarACBinary):

    _attr_device_class = BinarySensorDeviceClass.LOCK
    _attr_name = "Manual Lock Active"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_manual_lock"

    @cached_property
    def is_on(self) -> bool:
        now = dt_util.utcnow().timestamp()
        return any(
            until and until > now
            for until in self.coordinator.zone_manual_lock_until.values()
        )


class SolarACExportingBinarySensor(_BaseSolarACBinary):

    _attr_device_class = BinarySensorDeviceClass.POWER
    _attr_name = "Exporting"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_exporting"

    @cached_property
    def is_on(self) -> bool:
        return bool(getattr(self.coordinator, "ema_30s", 0) < 0)


class SolarACImportingBinarySensor(_BaseSolarACBinary):

    _attr_device_class = BinarySensorDeviceClass.POWER
    _attr_name = "Importing"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_importing"

    @cached_property
    def is_on(self) -> bool:
        return bool(getattr(self.coordinator, "ema_30s", 0) > 0)


class SolarACMasterBinarySensor(_BaseSolarACBinary):
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_name = "Master Switch"

    @cached_property
    def unique_id(self) -> str:
        return f"{self._entry_id}_master_switch"

    @cached_property
    def is_on(self) -> bool:
        ac_switch = self.coordinator.config.get(CONF_AC_SWITCH)
        if not ac_switch:
            return True
        state = self.coordinator.hass.states.get(ac_switch)
        if state is None:
            # Entity not yet available; treat as off for safety
            return False
        if state.state in ("unavailable", "unknown"):
            return False
        return bool(state.state == "on")
