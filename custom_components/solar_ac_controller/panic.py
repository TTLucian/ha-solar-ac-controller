"""Panic shedding logic for Solar AC Controller."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

from .const import CONF_AC_SWITCH

if TYPE_CHECKING:
    from .coordinator import SolarACCoordinator

_LOGGER = logging.getLogger(__name__)

_PANIC_COOLDOWN_SECONDS = 120


class PanicManager:

        def is_panicking(self) -> bool:
            """Return True if a panic event is currently active (panic task running or last action was panic)."""
            # Consider panic active if the panic task is running or last_action is 'panic'
            if getattr(self.coordinator, '_panic_task', None) and not self.coordinator._panic_task.done():
                return True
            if getattr(self.coordinator, 'last_action', None) == 'panic':
                return True
            return False
    """Manages emergency zone shedding when grid import exceeds panic threshold."""

    def __init__(self, coordinator: SolarACCoordinator) -> None:
        """Initialize panic manager."""
        self.coordinator = coordinator

    def should_panic(self, on_count: int) -> bool:
        """Return True if panic shedding should be triggered."""
        return (
            self.coordinator.ema_30s > self.coordinator.panic_threshold and on_count > 1
        )

    def is_in_cooldown(self, now_ts: float) -> bool:
        """Return True if in panic cooldown period."""
        if self.coordinator.last_panic_ts is None:
            return False
        return (now_ts - self.coordinator.last_panic_ts) < _PANIC_COOLDOWN_SECONDS

    async def schedule_panic(self, active_zones: list[str]) -> None:
        """Schedule panic task if not already running."""
        if self.coordinator.last_action != "panic":
            await self.coordinator._log(
                f"[PANIC_SHED_TRIGGER] ema30={round(self.coordinator.ema_30s)} "
                f"ema5m={round(self.coordinator.ema_5m)} "
                f"threshold={self.coordinator.panic_threshold} "
                f"zones={active_zones}"
            )
            if not self.coordinator._panic_task or self.coordinator._panic_task.done():
                self.coordinator._panic_task = self.coordinator.hass.async_create_task(
                    self._panic_task_runner(active_zones)
                )

    async def _panic_shed(self, active_zones: list[str]) -> None:
        """Shed all but the first active zone during panic."""
        start = dt_util.utcnow().timestamp()
        for zone in active_zones[1:]:
            await self.coordinator._call_entity_service(zone, False)
            await asyncio.sleep(self.coordinator.action_delay_seconds)
        end = dt_util.utcnow().timestamp()
        self.coordinator.last_action_start_ts = start
        self.coordinator.last_action_duration = end - start

    async def _panic_task_runner(self, active_zones: list[str]) -> None:
        """Run panic task with delay and learning reset."""
        try:
            if self.coordinator.panic_delay > 0:
                await asyncio.sleep(self.coordinator.panic_delay)

            # If master turned off during delay, abort
            ac_switch = self.coordinator.config.get(CONF_AC_SWITCH)
            if ac_switch:
                st = self.coordinator.hass.states.get(ac_switch)
                if st and st.state == "off":
                    await self.coordinator._log(
                        "[PANIC_ABORTED] master switch turned off during panic delay"
                    )
                    return

            if self.coordinator.ema_30s > self.coordinator.panic_threshold:
                await self._panic_shed(active_zones)

                # Reset learning state via controller if available
                try:
                    if getattr(self.coordinator, "controller", None) is not None:
                        await self.coordinator.controller._reset_learning_state_async()
                except Exception:
                    _LOGGER.debug(
                        "Controller reset learning method failed or controller not set"
                    )

                now_ts = dt_util.utcnow().timestamp()
                self.coordinator.last_panic_ts = now_ts

                await self.coordinator._log(
                    f"[PANIC_SHED] ema30={round(self.coordinator.ema_30s)} "
                    f"ema5m={round(self.coordinator.ema_5m)} zones={active_zones}"
                )

                self.coordinator.last_action = "panic"
        except asyncio.CancelledError:
            _LOGGER.debug("Panic task cancelled")
        except Exception as e:
            _LOGGER.exception("Error in panic task: %s", e)
        finally:
            self.coordinator._panic_task = None
