# custom_components/solar_ac_controller/diagnostic.py
from __future__ import annotations

from typing import Callable, Any
from homeassistant.components.sensor import SensorEntity
import logging

from .const import DOMAIN
from .helpers import build_diagnostics

_LOGGER = logging.getLogger(__name__)


class SolarACDiagnosticEntity(SensorEntity):
    """A single sensor exposing the entire controller state as JSON attributes."""

    _attr_should_poll = False
    _attr_name = "Solar AC Diagnostics"
    _attr_icon = "mdi:brain"

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        self.coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{self._entry_id}_diagnostics"


    @property
    def device_info(self):
        """Link to the 'Solar AC Controller' device (match all other entities)."""
        from homeassistant.helpers.entity import DeviceInfo
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            name="Solar AC Controller",
            sw_version=getattr(self.coordinator, "version", "0.5.1"),
            configuration_url="https://github.com/TTLucian/ha-solar-ac-controller",
        )

        self._unsub: Callable[[], None] | None = None

    @property
    def available(self) -> bool:
        ready = getattr(self.coordinator, "last_update_success", None)
        if isinstance(ready, bool):
            return ready
        return True

    async def async_added_to_hass(self) -> None:
        add_listener = getattr(self.coordinator, "async_add_listener", None)
        if callable(add_listener):
            try:
                self._unsub = add_listener(self.async_write_ha_state)
            except Exception as exc:
                _LOGGER.debug("Diagnostics: async_add_listener failed: %s", exc)
                self._unsub = None
                self.async_write_ha_state()
        else:
            self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if callable(self._unsub):
            try:
                self._unsub()
            except Exception as exc:
                _LOGGER.debug("Diagnostics: removal callable failed: %s", exc)
                try:
                    remove_listener = getattr(self.coordinator, "async_remove_listener", None)
                    if callable(remove_listener):
                        remove_listener(self.async_write_ha_state)
                except Exception:
                    _LOGGER.debug("Diagnostics: async_remove_listener fallback failed")
            finally:
                self._unsub = None
        else:
            try:
                remove_listener = getattr(self.coordinator, "async_remove_listener", None)
                if callable(remove_listener):
                    remove_listener(self.async_write_ha_state)
            except Exception:
                _LOGGER.debug("Diagnostics: async_remove_listener fallback failed (no removal callable)")

    @property
    def native_value(self) -> str:
        return getattr(self.coordinator, "last_action", None) or "idle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        try:
            attrs = build_diagnostics(self.coordinator)
            return attrs if isinstance(attrs, dict) else {}
        except Exception as exc:
            _LOGGER.debug("Diagnostics: build_diagnostics failed: %s", exc)
            return {
                "last_action": getattr(self.coordinator, "last_action", None),
                "samples": getattr(self.coordinator, "samples", None),
            }
