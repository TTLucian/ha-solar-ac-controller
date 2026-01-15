from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, CONF_AC_SWITCH, CONF_ZONES

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

    _attr_should_poll = False

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        self.coordinator = coordinator
        self._entry_id = entry_id
        # Device info shared across all entities
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "solar_ac_controller")},
            "name": "Solar AC Controller",
            "configuration_url": "https://github.com/TTLucian/ha-solar-ac-controller",
        }
        # Cache listener handle so we can remove it on unload
        self._listener = None

    async def async_added_to_hass(self) -> None:
        """Register update callback with the coordinator."""
        # Register a listener that writes the entity state when coordinator updates
        try:
            self._listener = self.coordinator.async_add_listener(self.async_write_ha_state)
        except Exception:
            # Fallback: if coordinator doesn't support async_add_listener, still attempt to write state
            _LOGGER.debug("Coordinator does not support async_add_listener; falling back to manual updates")
            self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Remove the coordinator listener when entity is removed/unloaded."""
        try:
            if self._listener:
                # DataUpdateCoordinator returns a callable removal function
                # If coordinator.async_add_listener returned a removal callable, call it.
                try:
                    self._listener()
                except Exception:
                    # If coordinator uses async_remove_listener pattern, try that
                    try:
                        self.coordinator.async_remove_listener(self.async_write_ha_state)
                    except Exception:
                        pass
                self._listener = None
            else:
                # Try to remove by method if available
                try:
                    self.coordinator.async_remove_listener(self.async_write_ha_state)
                except Exception:
                    pass
        except Exception:
            _LOGGER.debug("Failed to remove coordinator listener for %s", self.entity_id)

    @property
    def available(self) -> bool:
        """Return True if the entity is available.

        Consider the integration available when the coordinator exists and
        the coordinator update loop has run at least once. If coordinator
        does not expose a ready flag, fall back to True to avoid hiding UI.
        """
        if self.coordinator is None:
            return False
        # Prefer an explicit ready flag if coordinator exposes one
        ready = getattr(self.coordinator, "last_update_success", None)
        if isinstance(ready, bool):
            return ready
        # Fallback: assume available
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose a few diagnostic attributes for easier debugging in the UI."""
        try:
            return {
                "last_action": getattr(self.coordinator, "last_action", None),
                "last_action_start_ts": getattr(self.coordinator, "last_action_start_ts", None),
                "last_action_duration": getattr(self.coordinator, "last_action_duration", None),
                "samples": getattr(self.coordinator, "samples", None),
                "ema_30s": getattr(self.coordinator, "ema_30s", None),
                "ema_5m": getattr(self.coordinator, "ema_5m", None),
            }
        except Exception:
            return None


# ---------------------------------------------------------------------------
# BINARY SENSOR ENTITIES
# ---------------------------------------------------------------------------


class SolarACLearningBinarySensor(_BaseSolarACBinary):
    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)

    @property
    def name(self) -> str:
        return "Solar AC Learning Active"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_learning_active"

    @property
    def is_on(self) -> bool:
        return bool(getattr(self.coordinator, "learning_active", False))


class SolarACPanicBinarySensor(_BaseSolarACBinary):
    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)

    @property
    def name(self) -> str:
        return "Solar AC Panic State"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_panic_state"

    @property
    def is_on(self) -> bool:
        # Panic is true only during the actual shed event
        return getattr(self.coordinator, "last_action", None) == "panic"


class SolarACPanicCooldownBinarySensor(_BaseSolarACBinary):
    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)

    @property
    def name(self) -> str:
        return "Solar AC Panic Cooldown"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_panic_cooldown"

    @property
    def is_on(self) -> bool:
        ts = getattr(self.coordinator, "last_panic_ts", None)
        if not ts:
            return False
        now = dt_util.utcnow().timestamp()
        # Prefer coordinator-exposed cooldown if available
        cooldown = getattr(self.coordinator, "panic_cooldown_seconds", None)
        if cooldown is None:
            # Fallback to coordinator constant or default
            cooldown = getattr(self.coordinator, "_PANIC_COOLDOWN_SECONDS", 120)
        try:
            return (now - ts) < float(cooldown)
        except Exception:
            return (now - ts) < 120


class SolarACShortCycleBinarySensor(_BaseSolarACBinary):
    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)

    @property
    def name(self) -> str:
        return "Solar AC Short Cycling"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_short_cycling"

    @property
    def is_on(self) -> bool:
        c = self.coordinator
        now = dt_util.utcnow().timestamp()

        # Use CONF_ZONES constant for consistency
        for z in c.config.get(CONF_ZONES, []):
            last = c.zone_last_changed.get(z)
            if not last:
                continue

            last_type = c.zone_last_changed_type.get(z)
            if last_type == "on":
                threshold = getattr(c, "short_cycle_on_seconds", None) or c.short_cycle_on_seconds
            else:
                threshold = getattr(c, "short_cycle_off_seconds", None) or c.short_cycle_off_seconds

            try:
                if (now - last) < float(threshold):
                    return True
            except Exception:
                continue

        return False


class SolarACLockedBinarySensor(_BaseSolarACBinary):
    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)

    @property
    def name(self) -> str:
        return "Solar AC Manual Lock Active"

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
    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)

    @property
    def name(self) -> str:
        return "Solar AC Exporting"

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
    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)

    @property
    def name(self) -> str:
        return "Solar AC Importing"

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
    """Master switch sensor: ON when master is enabled, OFF when disabled."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)

    @property
    def name(self) -> str:
        return "Solar AC Master Switch"

    @property
    def unique_id(self) -> str:
        return f"{self._entry_id}_master_switch"

    @property
    def is_on(self) -> bool:
        """Return True when master is ON, False when master is OFF.

        If no master switch is configured, return True (integration runs).
        """
        ac_switch = self.coordinator.config.get(CONF_AC_SWITCH)
        if not ac_switch:
            # No physical master configured -> integration considered enabled
            return True

        switch_state_obj = self.coordinator.hass.states.get(ac_switch)
        if not switch_state_obj:
            # If entity missing, treat as OFF to be safe
            return False

        return switch_state_obj.state == "on"
