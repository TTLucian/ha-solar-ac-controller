from __future__ import annotations

from typing import Callable, Any

from homeassistant.components.sensor import SensorEntity

from .const import DOMAIN
from .helpers import build_diagnostics


class SolarACDiagnosticEntity(SensorEntity):
    """A single sensor exposing the entire controller state as JSON attributes."""

    _attr_should_poll = False
    _attr_name = "Solar AC Diagnostics"
    _attr_icon = "mdi:brain"
    # Omit device_class to avoid using a non-standard value

    def __init__(self, coordinator: Any, entry_id: str) -> None:
        """Create the diagnostics entity.

        Pass the coordinator and the config entry id so unique_id is stable
        across multiple installs.
        """
        self.coordinator = coordinator
        self._entry_id = entry_id

        # Unique per-entry id to avoid collisions across multiple installs
        self._attr_unique_id = f"{self._entry_id}_diagnostics"

        # Ensure sw_version is a plain string or None
        sw_version = getattr(coordinator, "version", None)
        try:
            sw_version = str(sw_version) if sw_version is not None else None
        except Exception:
            sw_version = None

        self._attr_device_info = {
            "identifiers": {(DOMAIN, "solar_ac_controller")},
            "name": "Solar AC Controller",
            "manufacturer": "TTLucian",
            "model": "Solar AC Smart Controller",
            "configuration_url": "https://github.com/TTLucian/ha-solar-ac-controller",
            "sw_version": sw_version,
        }

        self._unsub: Callable[[], None] | None = None

    @property
    def available(self) -> bool:
        """Return True if the entity is available.

        Prefer DataUpdateCoordinator's last_update_success if present.
        """
        ready = getattr(self.coordinator, "last_update_success", None)
        if isinstance(ready, bool):
            return ready
        return True

    async def async_added_to_hass(self) -> None:
        """Register for coordinator updates and keep unsubscribe handle.

        Be defensive: coordinator may not expose async_add_listener in tests
        or unusual runtime states.
        """
        add_listener = getattr(self.coordinator, "async_add_listener", None)
        if callable(add_listener):
            try:
                # DataUpdateCoordinator.async_add_listener returns a callable to unsubscribe
                self._unsub = add_listener(self.async_write_ha_state)
            except Exception:
                # Best-effort: fall back to writing initial state
                self._unsub = None
                self.async_write_ha_state()
        else:
            # Coordinator doesn't support listeners; write initial state
            self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up listener.

        Try the removal callable first; if not present, attempt coordinator removal API.
        """
        if callable(self._unsub):
            try:
                self._unsub()
            except Exception:
                # If the removal callable fails, attempt coordinator removal method
                try:
                    remove_listener = getattr(self.coordinator, "async_remove_listener", None)
                    if callable(remove_listener):
                        remove_listener(self.async_write_ha_state)
                except Exception:
                    pass
            finally:
                self._unsub = None
        else:
            # No removal callable stored; try coordinator removal API as a fallback
            try:
                remove_listener = getattr(self.coordinator, "async_remove_listener", None)
                if callable(remove_listener):
                    remove_listener(self.async_write_ha_state)
            except Exception:
                pass

    @property
    def native_value(self) -> str:
        """Expose last action as the main state."""
        return getattr(self.coordinator, "last_action", None) or "idle"

    @property
    def extra_state_attributes(self) -> dict:
        """Expose unified diagnostics attributes.

        Guard the helper to avoid raising during state updates.
        """
        try:
            attrs = build_diagnostics(self.coordinator)
            return attrs if isinstance(attrs, dict) else {}
        except Exception:
            # Avoid raising in property access; return minimal diagnostics
            return {
                "last_action": getattr(self.coordinator, "last_action", None),
                "samples": getattr(self.coordinator, "samples", None),
            }
