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
            f"add_conf={round(self.coordinator.last_add_conf)} export={round(export)} "
            f"req_export={round(required_export)} samples={self.coordinator.samples} "
            f"conf={round(self.coordinator.confidence)} "
            f"thr_add={self.coordinator.add_confidence_threshold} "
            f"thr_rem={self.coordinator.remove_confidence_threshold}"
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

        # Import here to avoid circular deps
        from .zones import ZoneManager

        zone_mgr = ZoneManager(self.coordinator)

        await self.coordinator._log(
            f"[ZONE_REMOVE_ATTEMPT] zone={last_zone} "
            f"remove_conf={round(self.coordinator.last_remove_conf)} "
            f"import={round(import_power)} "
            f"short_cycling={zone_mgr.is_short_cycling(last_zone)} "
            f"conf={round(self.coordinator.confidence)} "
            f"thr_add={self.coordinator.add_confidence_threshold} "
            f"thr_rem={self.coordinator.remove_confidence_threshold}"
        )
        await self.remove_zone(last_zone)
        self.coordinator.last_action = f"remove_{last_zone}"

    async def add_zone(self, zone: str, ac_power_before: float) -> None:
        """Start learning and turn on zone."""
        if self.coordinator.learning_active:
            await self.coordinator._log(
                f"[LEARNING_SKIPPED_ALREADY_ACTIVE] zone={zone} "
                f"current_zone={self.coordinator.learning_zone}"
            )
            return

        # Mark learning before action, but actual power delta is validated later
        self.coordinator.learning_band = self.coordinator.outside_band
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

        await asyncio.sleep(self.coordinator.action_delay_seconds)

        await self.coordinator._log(
            f"[ZONE_REMOVE_SUCCESS] zone={zone} import_after={round(self.coordinator.ema_5m)}"
        )

    async def call_entity_service(self, entity_id: str, turn_on: bool) -> None:
        """Call turn_on/turn_off service for the entity's domain, with climate fallback. If climate, set hvac_mode if needed."""
        domain = entity_id.split(".")[0]
        service = "turn_on" if turn_on else "turn_off"

        # If turning ON a climate entity, first turn on, then check/set hvac_mode
        if turn_on and domain == "climate":
            try:
                await self.coordinator.hass.services.async_call(
                    domain,
                    service,
                    {"entity_id": entity_id},
                    blocking=True,
                )
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
                except Exception as e:
                    _LOGGER.exception(
                        "Fallback climate.%s failed for %s: %s", service, entity_id, e
                    )
                    return
            # After turning on, check and set hvac_mode if needed
            state = self.coordinator.hass.states.get(entity_id)
            desired_mode = getattr(self.coordinator, "season_mode", "cool")
            if state:
                current_mode = state.attributes.get("hvac_mode")
                if current_mode != desired_mode:
                    await self.coordinator.hass.services.async_call(
                        "climate",
                        "set_hvac_mode",
                        {"entity_id": entity_id, "hvac_mode": desired_mode},
                        blocking=True,
                    )
            return

        # Non-climate or turn_off: original logic
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
