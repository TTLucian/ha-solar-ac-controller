from __future__ import annotations

import logging
from typing import Any, Callable
from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import dt as dt_util

from .const import DOMAIN, CONF_AC_SWITCH, CONF_ZONES
from .helpers import build_diagnostics

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
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
class _BaseSolarACBinary(BinarySensorEntity):
    """
    Base class for all Solar AC Controller binary sensors.
    Handles coordinator listener and device info.
    """
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        self.coordinator: Any = coordinator
        self._entry_id: str = entry_id
        self._listener: Callable[[], None] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Link to the 'Solar AC Controller' device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Solar AC Controller",
        )

    async def async_added_to_hass(self) -> None:
        """Register listener for coordinator updates."""
        self._listener = self.coordinator.async_add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        """Remove coordinator listener on entity removal."""
        if self._listener:
            self._listener()

# --- BINARY SENSORS ---
class SolarACLearningBinarySensor(_BaseSolarACBinary):
    _attr_name = "Learning Active"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_learning_active"
    @property
    def is_on(self) -> bool: return bool(getattr(self.coordinator, "learning_active", False))

class SolarACPanicBinarySensor(_BaseSolarACBinary):
    _attr_name = "Panic State"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_panic_state"
    @property
    def is_on(self) -> bool: return getattr(self.coordinator, "last_action", None) == "panic"

class SolarACPanicCooldownBinarySensor(_BaseSolarACBinary):
    _attr_name = "Panic Cooldown"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_panic_cooldown_bin"
    @property
    def is_on(self) -> bool:
        ts = getattr(self.coordinator, "last_panic_ts", None)
        if not ts: return False
        cooldown = getattr(self.coordinator, "panic_cooldown_seconds", 120)
        return (dt_util.utcnow().timestamp() - float(ts)) < float(cooldown)

class SolarACShortCycleBinarySensor(_BaseSolarACBinary):
    _attr_name = "Short Cycling"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_short_cycling"
    @property
    def is_on(self) -> bool:
        now = dt_util.utcnow().timestamp()
        for z in self.coordinator.config.get(CONF_ZONES, []):
            if (last := self.coordinator.zone_last_changed.get(z)):
                threshold = self.coordinator.short_cycle_on_seconds if self.coordinator.zone_last_changed_type.get(z) == "on" else self.coordinator.short_cycle_off_seconds
                if (now - last) < float(threshold): return True
        return False

class SolarACLockedBinarySensor(_BaseSolarACBinary):
    _attr_name = "Manual Lock Active"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_manual_lock"
    @property
    def is_on(self) -> bool:
        now = dt_util.utcnow().timestamp()
        return any(until and until > now for until in self.coordinator.zone_manual_lock_until.values())

class SolarACExportingBinarySensor(_BaseSolarACBinary):
    _attr_name = "Exporting"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_exporting"
    @property
    def is_on(self) -> bool: return bool(getattr(self.coordinator, "ema_30s", 0) < 0)

class SolarACImportingBinarySensor(_BaseSolarACBinary):
    _attr_name = "Importing"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_importing"
    @property
    def is_on(self) -> bool: return bool(getattr(self.coordinator, "ema_30s", 0) > 0)

class SolarACMasterBinarySensor(_BaseSolarACBinary):
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_name = "Master Switch"
    @property
    def unique_id(self) -> str: return f"{self._entry_id}_master_switch"
    @property
    def is_on(self) -> bool:
        ac_switch = self.coordinator.config.get(CONF_AC_SWITCH)
        if not ac_switch: return True
        return (state := self.coordinator.hass.states.get(ac_switch)) and state.state == "on"
