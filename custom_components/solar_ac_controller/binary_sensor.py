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


class _BaseSolarACBinary(BinarySensorEntity):
    _attr_should_poll = False

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        self.coordinator = coordinator
        self._entry_id = entry_id
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "solar_ac_controller")},
            "name": "Solar AC Controller",
            "configuration_url": "https://github.com/TTLucian/ha-solar-ac-controller",
        }
        self._listener = None

    async def async_added_to_hass(self) -> None:
        try:
            self._listener = self.coordinator.async_add_listener(self.async_write_ha_state)
        except Exception:
            _LOGGER.debug("Coordinator does not support async_add_listener; falling back to manual updates")
            self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        try:
            if self._listener:
                try:
                    self._listener()
                except Exception:
                    try:
                        self.coordinator.async_remove_listener(self.async_write_ha_state)
                    except Exception:
                        pass
                self._listener = None
            else:
                try:
                    self.coordinator.async_remove_listener(self.async_write_ha_state)
                except Exception:
                    pass
        except Exception:
            _LOGGER.debug("Failed to remove coordinator listener for %s", getattr(self, "entity_id", None))

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
        """Expose diagnostics but avoid exposing epoch timestamps."""
        try:
            return {
                "last_action": getattr(self.coordinator, "last_action", None),
                "samples": getattr(self.coordinator, "samples", None),
                "ema_30s": getattr(self.coordinator, "ema_30s", None),
                "ema_5m": getattr(self.coordinator, "ema_5m", None),
            }
        except Exception:
            return None


# (Binary sensor entity classes unchanged except unique_id prefixes and using the base class)
# ... (reuse the entity classes from the previous binary_sensor patch; they remain valid)
