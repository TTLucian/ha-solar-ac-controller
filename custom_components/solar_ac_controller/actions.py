"""Zone control actions for Solar AC Controller."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import CONF_ZONES

if TYPE_CHECKING:
    from .coordinator import SolarACCoordinator

_LOGGER = logging.getLogger(__name__)


class ActionExecutor:
    """Executes zone control actions: add, remove, and service calls."""

    def __init__(self, coordinator: SolarACCoordinator) -> None:
        """Initialize action executor."""
        self.coordinator = coordinator
        self._action_lock = asyncio.Lock()

    async def attempt_add_zone(
        self,
        next_zone: str,
        ac_power_before: float,
        export: float,
        required_export: float,
    ) -> None:
        """Log and execute add zone action."""
        # Validate zone exists in configuration
        configured_zones = self.coordinator.config.get(CONF_ZONES, [])
        if next_zone not in configured_zones:
            raise HomeAssistantError(
                f"Invalid zone '{next_zone}': not in configured zones {configured_zones}"
            )

        if self.coordinator.last_action == f"add_{next_zone}":
            return

        async with self._action_lock:
            await self.add_zone(next_zone, ac_power_before)
        async with self.coordinator._state_lock:
            self.coordinator.last_action = f"add_{next_zone}"

    async def attempt_remove_zone(
        self,
        last_zone: str,
        import_power: float,
    ) -> None:
        """Log and execute remove zone action."""
        # Validate zone exists in configuration
        configured_zones = self.coordinator.config.get(CONF_ZONES, [])
        if last_zone not in configured_zones:
            raise HomeAssistantError(
                f"Invalid zone '{last_zone}': not in configured zones {configured_zones}"
            )

        if self.coordinator.last_action == f"remove_{last_zone}":
            return

        zone_mgr = self.coordinator.zone_manager

        await self.coordinator._log(
            f"Deactivating zone '{last_zone.split('.')[-1]}' - "
            f"confidence score {round(self.coordinator.confidence, 1)} below deactivation threshold, "
            f"grid import {round(import_power)}W, "
            f"short cycling protection: {zone_mgr.is_short_cycling(last_zone)}"
        )

        async with self._action_lock:
            await self.remove_zone(last_zone)
        async with self.coordinator._state_lock:
            self.coordinator.last_action = f"remove_{last_zone}"

    async def add_zone(self, zone: str, ac_power_before: float) -> None:
        """Start learning and turn on zone."""
        # Validate zone exists in configuration
        configured_zones = self.coordinator.config.get(CONF_ZONES, [])
        if zone not in configured_zones:
            raise HomeAssistantError(
                f"Invalid zone '{zone}': not in configured zones {configured_zones}"
            )

        if await self.coordinator.controller.is_learning_active():
            current_learning_zone = await self.coordinator.controller.session.get_zone()
            await self.coordinator._log(
                f"Power learning for '{zone.split('.')[-1]}' skipped - "
                f"another zone ('{current_learning_zone.split('.')[-1] if current_learning_zone else 'unknown'}') "
                f"is currently being measured"
            )
            await self.add_zone_without_learning(zone, ac_power_before)
            return

        # Mark learning before action, but actual power delta is validated later
        await self.coordinator.controller.start_learning(zone, ac_power_before)

        start = dt_util.utcnow().timestamp()
        try:
            await self.call_entity_service(zone, True)
        finally:
            now_ts = dt_util.utcnow().timestamp()
            self.coordinator.last_action_start_ts = start
            self.coordinator.last_action_duration = now_ts - start
            self.coordinator.zone_last_changed[zone] = now_ts
            self.coordinator.zone_last_changed_type[zone] = "on"

        # Notify learning session of zone addition (for contamination detection)
        await self.coordinator.controller.session.notify_zone_changed_during_learning(
            zone, "add"
        )

        # Check for cancellation before delay
        if self.coordinator.hass.is_stopping:
            return

        await asyncio.sleep(self.coordinator.action_delay_seconds)

        await self.coordinator._log(
            f"[ADD_ZONE_MEASURING] zone='{zone.split('.')[-1]}' "
            f"ac_before={round(ac_power_before)}W - "
            f"measuring power increase to determine zone requirements"
        )

    async def add_zone_without_learning(
        self, zone: str, ac_power_before: float
    ) -> None:
        """Turn on zone without starting learning (for multi-zone additions)."""
        # Validate zone exists in configuration
        configured_zones = self.coordinator.config.get(CONF_ZONES, [])
        if zone not in configured_zones:
            raise HomeAssistantError(
                f"Invalid zone '{zone}': not in configured zones {configured_zones}"
            )

        start = dt_util.utcnow().timestamp()
        try:
            await self.call_entity_service(zone, True)
        finally:
            now_ts = dt_util.utcnow().timestamp()
            self.coordinator.last_action_start_ts = start
            self.coordinator.last_action_duration = now_ts - start
            self.coordinator.zone_last_changed[zone] = now_ts
            self.coordinator.zone_last_changed_type[zone] = "on"

        # Notify learning session of zone addition (for contamination detection)
        await self.coordinator.controller.session.notify_zone_changed_during_learning(
            zone, "add"
        )

        # Check for cancellation before delay
        if self.coordinator.hass.is_stopping:
            return

        await asyncio.sleep(self.coordinator.action_delay_seconds)

        await self.coordinator._log(
            f"Activating zone '{zone.split('.')[-1]}' using previously learned power consumption data"
        )

    async def remove_zone(self, zone: str) -> None:
        """Turn off zone and update short-cycle memory."""
        # Validate zone exists in configuration
        configured_zones = self.coordinator.config.get(CONF_ZONES, [])
        if zone not in configured_zones:
            raise HomeAssistantError(
                f"Invalid zone '{zone}': not in configured zones {configured_zones}"
            )

        start = dt_util.utcnow().timestamp()
        try:
            await self.call_entity_service(zone, False)
        finally:
            now_ts = dt_util.utcnow().timestamp()
            self.coordinator.last_action_start_ts = start
            self.coordinator.last_action_duration = now_ts - start
            self.coordinator.zone_last_changed[zone] = now_ts
            self.coordinator.zone_last_changed_type[zone] = "off"

        # Notify learning session of zone removal (for contamination detection)
        await self.coordinator.controller.session.notify_zone_changed_during_learning(
            zone, "remove"
        )

        # Set compressor recovery window to avoid rapid re-adds until hardware ramps
        try:
            now_ts = dt_util.utcnow().timestamp()
            ramp = getattr(self.coordinator, "compressor_ramp_seconds", 0) or 0
            if ramp and hasattr(self.coordinator, "compressor_recover_until"):
                self.coordinator.compressor_recover_until = now_ts + float(ramp)
                await self.coordinator._log(
                    f"[COMPRESSOR] set recovery until {int(self.coordinator.compressor_recover_until)} (ramp={int(ramp)}s)",
                    "debug",
                )
        except Exception:
            # Defensive: do not break zone removal on logging failures
            pass

        # Check for cancellation before delay
        if self.coordinator.hass.is_stopping:
            return

        await asyncio.sleep(self.coordinator.action_delay_seconds)

        await self.coordinator._log(
            f"Zone '{zone.split('.')[-1]}' deactivated successfully - "
            f"grid import now {round(self.coordinator.ema_5m)}W"
        )

    async def call_entity_service(
        self,
        entity_id: str,
        turn_on: bool,
        reason: str = "",
        confidence: float | None = None,
        export_margin: float | None = None,
    ) -> None:
        """Call turn_on/turn_off service for the entity's domain, with climate fallback.

        A fresh :class:`homeassistant.core.Context` is created for every call so
        that subsequent state-change events carry a known context-ID.  The ID is
        stored on the coordinator and used by the override-detection logic in
        zones.py to distinguish integration-issued commands from manual changes.
        """
        domain = entity_id.split(".")[0]
        service = "turn_on" if turn_on else "turn_off"
        action_label = "on" if turn_on else "off"

        # One context for the entire operation (primary + optional hvac_mode follow-up).
        ctx = Context()
        issued_ts = dt_util.utcnow().timestamp()

        def _store_context() -> None:
            """Record issued context on coordinator for authorship tracking."""
            self.coordinator.zone_last_context_id[entity_id] = (ctx.id, issued_ts)

        def _record() -> None:
            """Append an integration-sourced action to the zone history ring buffer."""
            self.coordinator._record_zone_action(
                entity_id,
                action_label,
                source="integration",
                reason=reason,
                confidence=confidence,
                export_margin=export_margin,
            )

        # Primary: attempt domain.turn_on/turn_off first
        try:
            await self.coordinator.hass.services.async_call(
                domain,
                service,
                {"entity_id": entity_id},
                blocking=True,
                context=ctx,
            )

            # If we turned on a climate entity, verify hvac_mode and set it only if needed
            if (
                turn_on
                and domain == "climate"
                and self.coordinator.season_mode in ("heat", "cool")
            ):
                try:
                    st = self.coordinator.hass.states.get(entity_id)
                    current_mode = None
                    if st and isinstance(st.attributes, dict):
                        current_mode = st.attributes.get("hvac_mode")

                    if current_mode != self.coordinator.season_mode:
                        try:
                            await self.coordinator.hass.services.async_call(
                                "climate",
                                "set_hvac_mode",
                                {
                                    "entity_id": entity_id,
                                    "hvac_mode": self.coordinator.season_mode,
                                },
                                blocking=True,
                                context=ctx,
                            )
                            _LOGGER.debug(
                                "Set HVAC mode to '%s' for %s after turning on",
                                self.coordinator.season_mode,
                                entity_id,
                            )
                        except (ValueError, TypeError, AttributeError, KeyError) as e:
                            _LOGGER.warning(
                                "Failed to set HVAC mode '%s' for %s after turn_on: %s",
                                self.coordinator.season_mode,
                                entity_id,
                                e,
                            )
                except (
                    Exception
                ) as e:  # defensive - don't break main flow for unexpected state issues
                    _LOGGER.debug("Could not verify hvac_mode for %s: %s", entity_id, e)

            _store_context()
            _record()
            return
        except (
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
            HomeAssistantError,
        ) as e:
            _LOGGER.debug(
                "Primary service %s.%s failed for %s: %s — attempting climate fallback",
                domain,
                service,
                entity_id,
                e,
            )

        # Fallback: attempt climate.<turn_on|turn_off>
        try:
            await self.coordinator.hass.services.async_call(
                "climate",
                service,
                {"entity_id": entity_id},
                blocking=True,
                context=ctx,
            )
            _LOGGER.warning(
                "Primary service %s.%s failed for %s — used climate.%s as fallback",
                domain,
                service,
                entity_id,
                service,
            )
            _store_context()
            _record()
            return
        except (
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
            HomeAssistantError,
        ) as e:
            _LOGGER.exception(
                "Fallback climate.%s failed for %s: %s", service, entity_id, e
            )
            raise HomeAssistantError(
                f"Failed to {service} {entity_id} - entity unavailable or unresponsive"
            )
