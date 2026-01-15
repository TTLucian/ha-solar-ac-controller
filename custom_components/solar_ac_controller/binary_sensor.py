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
    """Set up binary sensors for the Solar AC Controller integration."""
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


# ---------------------------------------------------------------------------
# BASE CLASS
# ---------------------------------------------------------------------------

class _BaseSolarACBinary(BinarySensorEntity):
    """Base class for all Solar AC binary sensors."""
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        self.coordinator = coordinator
        self._entry_id = entry_id
        self._listener: Callable[[], None] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Link this binary sensor to the branded 'TTLucian' device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Solar AC Controller",
            manufacturer="TTLucian",
            model="Solar AC Logic Controller",
            sw_version=getattr(self.coordinator, "version", "0.5.0"),
        )

    async def async_added_to_hass(self) -> None:
        try:
            self._listener = self.coordinator.async_add_listener(self.async_write_ha_state)
        except Exception:
            self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._listener:
            self._listener()
            self._listener = None

    @property
    def available(self) -> bool:
        if self.coordinator is None:
            return False
        ready = getattr(self.coordinator, "last_update_success", None)
        if isinstance(ready, bool):
            return ready
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose concise diagnostics and required_export context."""
        try:
            diag = build_diagnostics(self.coordinator)
            return {
                "last_action": getattr(self.coordinator, "last_action", None),
                "samples": getattr(self.coordinator, "samples", None),
                "ema_30s": getattr(self.coordinator, "ema_30s", None),
                "ema_5m": getattr(self.coordinator, "ema_5m", None),
                "required_export": getattr(self.coordinator, "required_export", None),
                "export_margin": getattr(self.coordinator, "export_margin", None),
                "required_export_source": diag.get("required_export_source"),
                "master_off": diag.get("master_off"),
                "last_panic": diag.get("last_panic"),
            }
        except Exception:
            return None


# ---------------------------------------------------------------------------
# BINARY SENSOR ENTITIES
# ---------------------------------------------------------------------------

class SolarACLearningBinarySensor(_BaseSolarACBinary):
    @property
    def name(self) -> str:
        return "Learning Active"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_learning_active"

    @property
    def is_on(self) -> bool:
        return bool(getattr(self.coordinator, "learning_active", False))


class SolarACPanicBinarySensor(_BaseSolarACBinary):
    @property
    def name(self) -> str:
        return "Panic State"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_panic_state"

    @property
    def is_on(self) -> bool:
        return getattr(self.coordinator, "last_action", None) == "panic"


class SolarACPanicCooldownBinarySensor(_BaseSolarACBinary):
    @property
    def name(self) -> str:
        return "Panic Cooldown"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_panic_cooldown"

    @property
    def is_on(self) -> bool:
        ts = getattr(self.coordinator, "last_panic_ts", None)
        if not ts:
            return False
        cooldown = getattr(self.coordinator, "panic_cooldown_seconds", None) or getattr(self.coordinator, "_PANIC_COOLDOWN_SECONDS", 120)
        try:
            now = dt_util.utcnow().timestamp()
            return (now - float(ts)) < float(cooldown)
        except Exception:
            return False


class SolarACShortCycleBinarySensor(_BaseSolarACBinary):
    @property
    def name(self) -> str:
        return "Short Cycling"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_short_cycling"

    @property
    def is_on(self) -> bool:
        c = self.coordinator
        now = dt_util.utcnow().timestamp()

        for z in c.config.get(CONF_ZONES, []):
            last = c.zone_last_changed.get(z)
            if not last:
                continue

            last_type = c.zone_last_changed_type.get(z)
            threshold = c.short_cycle_on_seconds if last_type == "on" else c.short_cycle_off_seconds

            try:
                if (now - last) < float(threshold):
                    return True
            except Exception:
                continue

        return False


class SolarACLockedBinarySensor(_BaseSolarACBinary):
    @property
    def name(self) -> str:
        return "Manual Lock Active"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_manual_lock"

    @property
    def is_on(self) -> bool:
        now = dt_util.utcnow().timestamp()
        return any(
            until and until > now
            for until in self.coordinator.zone_manual_lock_until.values()
        )


class SolarACExportingBinarySensor(_BaseSolarACBinary):
    @property
    def name(self) -> str:
        return "Exporting"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_exporting"

    @property
    def is_on(self) -> bool:
        try:
            return bool(getattr(self.coordinator, "ema_30s", 0) < 0)
        except Exception:
            return False


class SolarACImportingBinarySensor(_BaseSolarACBinary):
    @property
    def name(self) -> str:
        return "Importing"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_importing"

    @property
    def is_on(self) -> bool:
        try:
            return bool(getattr(self.coordinator, "ema_30s", 0) > 0)
        except Exception:
            return False


class SolarACMasterBinarySensor(_BaseSolarACBinary):
    """Master switch sensor: ON when master is enabled, OFF when master is disabled."""
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def name(self) -> str:
        return "Master Switch"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_master_switch"

    @property
    def is_on(self) -> bool:
        ac_switch = self.coordinator.config.get(CONF_AC_SWITCH)
        if not ac_switch:
            return True

        switch_state_obj = self.coordinator.hass.states.get(ac_switch)
        if not switch_state_obj:
            return False

        return switch_state_obj.state == "on"
