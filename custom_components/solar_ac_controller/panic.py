"""Panic shedding logic for Solar AC Controller."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast

from homeassistant.util import dt as dt_util

from .const import CONF_AC_SWITCH, PANIC_COOLDOWN_SECONDS

if TYPE_CHECKING:
    from .coordinator import SolarACCoordinator

_LOGGER = logging.getLogger(__name__)


class PanicManager:
    """Manages emergency zone shedding when grid import exceeds panic threshold."""

    def __init__(self, coordinator: "SolarACCoordinator") -> None:
        self.coordinator = coordinator
        self._cancel_requested = False

    @property
    def is_panicking(self) -> bool:
        """Return True if a panic task is currently running (zones are being shed)."""
        return (
            self.coordinator._panic_task is not None
            and not self.coordinator._panic_task.done()
        )

    @property
    def should_panic(self) -> bool:
        """Return True if panic shedding should be triggered."""
        # Use on_count from coordinator if available, else default to 2
        on_count = getattr(self.coordinator, "on_count", 2)
        return (
            self.coordinator.ema_30s > self.coordinator.panic_threshold and on_count > 0
        )

    @property
    def is_in_cooldown(self) -> bool:
        """Return True if in panic cooldown period."""
        now_ts = dt_util.utcnow().timestamp()
        if self.coordinator.last_panic_ts is None:
            return False
        return cast(
            bool, (now_ts - self.coordinator.last_panic_ts) < PANIC_COOLDOWN_SECONDS
        )

    async def cancel_panic(self) -> None:
        """Request panic cancellation and cancel any running panic task."""
        self._cancel_requested = True

        # Safely access panic task state under lock
        async with self.coordinator._state_lock:
            self.coordinator._panic_active = False
            task = self.coordinator._panic_task

        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                # Expected during cooperative cancellation
                pass
            except (AttributeError, TypeError) as exc:
                _LOGGER.debug("Error during panic task cancellation: %s", exc)
            finally:
                # Clear task reference after cancellation
                async with self.coordinator._state_lock:
                    if self.coordinator._panic_task is task:
                        self.coordinator._panic_task = None

    async def schedule_panic(self, active_zones: list[str]) -> None:
        """Schedule panic task if not already running."""
        async with self.coordinator._state_lock:
            self.coordinator._panic_active = True  # Prevent decision overrides
            task_exists = (
                self.coordinator._panic_task is not None
                and not self.coordinator._panic_task.done()
            )

        if self.coordinator.last_action != "panic":
            await self.coordinator._log(
                f"[PANIC_SHED_TRIGGER] ema30={round(self.coordinator.ema_30s)} "
                f"ema5m={round(self.coordinator.ema_5m)} "
                f"threshold={self.coordinator.panic_threshold} "
                f"zones={active_zones}"
            )

        if not task_exists:
            # Reset any previous cancellation request when starting a new panic
            self._cancel_requested = False
            async with self.coordinator._state_lock:
                self.coordinator._panic_task = self.coordinator.create_background_task(
                    self._panic_task_runner(active_zones)
                )

    async def _panic_shed(self, active_zones: list[str]) -> None:
        """Shed zones during panic.

        Multi-zone: keep the first zone running, remove the rest.
        Single-zone: remove the only zone — there is nothing safe to keep on.
        """
        start = dt_util.utcnow().timestamp()
        # With multiple zones we preserve the first (lowest-index) zone as a
        # minimum load.  With only one zone active there is nothing to fall back
        # to, so that zone must be shed as well.
        zones_to_shed = active_zones[1:] if len(active_zones) > 1 else active_zones
        for zone in zones_to_shed:
            await self.coordinator.action_executor.call_entity_service(zone, False)
            # Update short-cycle tracking so protection kicks in after panic cooldown
            now_ts = dt_util.utcnow().timestamp()
            self.coordinator.zone_last_changed[zone] = now_ts
            self.coordinator.zone_last_changed_type[zone] = "off"
            # Notify learning session of panic removal (for contamination detection)
            await self.coordinator.controller.session.notify_zone_changed_during_learning(
                zone, "panic"
            )
            await asyncio.sleep(self.coordinator.action_delay_seconds)
        end = dt_util.utcnow().timestamp()
        self.coordinator.last_action_start_ts = start
        self.coordinator.last_action_duration = end - start

    async def _panic_task_runner(self, active_zones: list[str]) -> None:
        """Run panic task with delay and learning reset."""
        try:
            if self.coordinator.panic_delay > 0:
                await asyncio.sleep(self.coordinator.panic_delay)
                if self._cancel_requested:
                    _LOGGER.debug("Panic task cancelled during delay before shedding")
                    return

                # Re-read active zones after the delay — they may have changed while
                # we were waiting (e.g. manual override, freeze, another panic cycle).
                refreshed = list(getattr(self.coordinator, "active_zones", None) or [])
                if refreshed:
                    active_zones = refreshed

            # If master turned off during delay, abort
            ac_switch = self.coordinator.config.get(CONF_AC_SWITCH)
            if ac_switch:
                st = self.coordinator.hass.states.get(ac_switch)
                if st and st.state == "off":
                    await self.coordinator._log(
                        "[PANIC_ABORTED] master switch turned off during panic delay"
                    )
                    return

            if self._cancel_requested:
                _LOGGER.debug("Panic task cancelled before evaluating panic condition")
                return

            # Abort if integration was disabled while waiting for the panic delay
            if not getattr(self.coordinator, "integration_enabled", True):
                await self.coordinator._log(
                    "[PANIC_ABORTED] integration disabled during panic delay"
                )
                return

            if self.coordinator.ema_30s > self.coordinator.panic_threshold:
                await self._panic_shed(active_zones)

                # Reset learning state via controller if available
                try:
                    if getattr(self.coordinator, "controller", None) is not None:
                        await self.coordinator.controller._reset_learning_state_async()
                except (AttributeError, asyncio.CancelledError):
                    _LOGGER.debug(
                        "Controller reset learning method failed or controller not set"
                    )

                now_ts = dt_util.utcnow().timestamp()
                self.coordinator.last_panic_ts = now_ts

                await self.coordinator._log(
                    f"[PANIC_SHED] ema30={round(self.coordinator.ema_30s)} "
                    f"ema5m={round(self.coordinator.ema_5m)} zones={active_zones}"
                )

                async with self.coordinator._state_lock:
                    self.coordinator.last_action = "panic"
        except asyncio.CancelledError:
            _LOGGER.debug("Panic task cancelled")
        except (ValueError, TypeError, AttributeError, KeyError) as e:
            _LOGGER.exception("Error in panic task: %s", e)
        finally:
            async with self.coordinator._state_lock:
                self.coordinator._panic_task = None
                self.coordinator._panic_active = False
