"""Zone control actions for Solar AC Controller."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .coordinator import SolarACCoordinator

_LOGGER = logging.getLogger(__name__)


class ActionExecutor:
    """Executes zone control actions: add, remove, and service calls."""

    def __init__(self, coordinator: SolarACCoordinator) -> None:
        """Initialize action executor."""
        self.coordinator = coordinator

    async def attempt_add_zone(
        self,
        next_zone: str,
        ac_power_before: float,
        export: float,
        required_export: float,
    ) -> None:
        """Log and execute add zone action."""
        if self.coordinator.last_action == f"add_{next_zone}":
            return

        await self.coordinator._log(
            f"[ZONE_ADD_ATTEMPT] zone={next_zone} "
            f"unified_conf={round(self.coordinator.confidence)} "
            f"(add={round(self.coordinator.last_add_conf)}, remove={round(self.coordinator.last_remove_conf)}) "
            f"export={round(export)} req_export={round(required_export)} samples={self.coordinator.samples} "
            f"threshold={self.coordinator.unified_add_threshold}"
        )

        await self.add_zone(next_zone, ac_power_before)
        self.coordinator.last_action = f"add_{next_zone}"

    async def attempt_remove_zone(
        self,
        last_zone: str,
        import_power: float,
    ) -> None:
        """Log and execute remove zone action."""
        if self.coordinator.last_action == f"remove_{last_zone}":
            return

        zone_mgr = self.coordinator.zone_manager

        await self.coordinator._log(
            f"[ZONE_REMOVE_ATTEMPT] zone={last_zone} "
            f"unified_conf={round(self.coordinator.confidence)} "
            f"(add={round(self.coordinator.last_add_conf)}, remove={round(self.coordinator.last_remove_conf)}) "
            f"import={round(import_power)} short_cycling={zone_mgr.is_short_cycling(last_zone)} "
            f"threshold={self.coordinator.unified_remove_threshold}"
        )
        await self.remove_zone(last_zone)
        self.coordinator.last_action = f"remove_{last_zone}"

    async def add_zone(self, zone: str, ac_power_before: float) -> None:
        """Start learning and turn on zone."""
        if await self.coordinator.controller.is_learning_active():
            await self.coordinator._log(
                f"[LEARNING_SKIPPED_ALREADY_ACTIVE] zone={zone} "
                f"current_zone={self.coordinator.learning_zone}"
            )
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

        # Check for cancellation before delay
        if self.coordinator.hass.is_stopping:
            return

        await asyncio.sleep(self.coordinator.action_delay_seconds)

        await self.coordinator._log(
            f"[LEARNING_START] zone={zone} ac_before={round(ac_power_before)} "
            f"samples={self.coordinator.samples}"
        )

    async def remove_zone(self, zone: str) -> None:
        """Turn off zone and update short-cycle memory."""
        start = dt_util.utcnow().timestamp()
        try:
            await self.call_entity_service(zone, False)
        finally:
            now_ts = dt_util.utcnow().timestamp()
            self.coordinator.last_action_start_ts = start
            self.coordinator.last_action_duration = now_ts - start
            self.coordinator.zone_last_changed[zone] = now_ts
            self.coordinator.zone_last_changed_type[zone] = "off"

        # Check for cancellation before delay
        if self.coordinator.hass.is_stopping:
            return

        await asyncio.sleep(self.coordinator.action_delay_seconds)

        await self.coordinator._log(
            f"[ZONE_REMOVE_SUCCESS] zone={zone} import_after={round(self.coordinator.ema_5m)}"
        )

    async def call_entity_service(self, entity_id: str, turn_on: bool) -> None:
        """Call turn_on/turn_off service for the entity's domain, with climate fallback."""
        domain = entity_id.split(".")[0]
        service = "turn_on" if turn_on else "turn_off"

        # For climate entities being turned on: set HVAC mode first based on season
        if (
            turn_on
            and domain == "climate"
            and self.coordinator.season_mode in ("heat", "cool")
        ):
            try:
                await self.coordinator.hass.services.async_call(
                    "climate",
                    "set_hvac_mode",
                    {"entity_id": entity_id, "hvac_mode": self.coordinator.season_mode},
                    blocking=True,
                )
                _LOGGER.debug(
                    "Set HVAC mode to '%s' for %s before turning on",
                    self.coordinator.season_mode,
                    entity_id,
                )
            except Exception as e:
                _LOGGER.warning(
                    "Failed to set HVAC mode '%s' for %s: %s — will proceed with turn_on",
                    self.coordinator.season_mode,
                    entity_id,
                    e,
                )

        try:
            await self.coordinator.hass.services.async_call(
                domain,
                service,
                {"entity_id": entity_id},
                blocking=True,
            )
            return
        except Exception as e:
            _LOGGER.debug(
                "Primary service %s.%s failed for %s: %s",
                domain,
                service,
                entity_id,
                e,
            )

        try:
            await self.coordinator.hass.services.async_call(
                "climate",
                service,
                {"entity_id": entity_id},
                blocking=True,
            )
            _LOGGER.warning(
                "Primary service %s.%s failed for %s — used climate.%s as fallback",
                domain,
                service,
                entity_id,
                service,
            )
            return
        except Exception as e:
            _LOGGER.exception(
                "Fallback climate.%s failed for %s: %s", service, entity_id, e
            )
