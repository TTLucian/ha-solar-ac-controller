# custom_components/solar_ac_controller/controller.py
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, cast

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .exceptions import StorageError

_LOGGER = logging.getLogger(__name__)


@dataclass
class LearningResult:
    success: bool
    learned_power: Optional[float] = None
    error_message: Optional[str] = None


class LearningSession:
    """Encapsulates learning state with thread-safe access."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active = False
        self._zone: Optional[str] = None
        self._start_time: Optional[float] = None
        self._samples = 0

    async def is_active(self) -> bool:
        async with self._lock:
            return self._active

    async def start_session(self, zone: str, start_time: float) -> None:
        async with self._lock:
            self._active = True
            self._zone = zone
            self._start_time = start_time

    async def end_session(self) -> None:
        async with self._lock:
            self._active = False
            self._zone = None
            self._start_time = None

    async def get_zone(self) -> Optional[str]:
        async with self._lock:
            return self._zone

    async def get_start_time(self) -> Optional[float]:
        async with self._lock:
            return self._start_time

    async def increment_samples(self) -> None:
        async with self._lock:
            self._samples += 1

    async def get_samples(self) -> int:
        async with self._lock:
            return self._samples


class SolarACController:
    """
    Controller helper that encapsulates learning operations and persistence.
    All learning state is managed on the coordinator.
    """

    def __init__(
        self, hass: HomeAssistant, coordinator: Any, store: Any | None = None
    ) -> None:
        """Initialize controller with Home Assistant, coordinator, and optional store."""
        self.hass = hass
        self.coordinator = coordinator
        self.store = store
        self.session = LearningSession()

    async def is_learning_active(self) -> bool:
        """Check if learning is active, with proper locking."""
        return await self.session.is_active()

    async def start_learning(
        self, zone_entity_id: str, ac_power_before: float | None
    ) -> None:
        """Begin learning for a zone, storing baseline power."""
        if await self.session.is_active():
            _LOGGER.debug(
                "start_learning called but learning already active for zone=%s",
                await self.session.get_zone(),
            )
            return

        try:
            baseline = float(ac_power_before) if ac_power_before is not None else None
        except (TypeError, ValueError):
            baseline = None
            _LOGGER.debug("start_learning: invalid ac_power_before=%s", ac_power_before)

        start_time = dt_util.utcnow().timestamp()
        await self.session.start_session(zone_entity_id, start_time)
        self.coordinator.learning_zone = zone_entity_id
        self.coordinator.learning_start_time = start_time
        self.coordinator.ac_power_before = baseline

        _LOGGER.debug(
            "Start learning: zone=%s ac_before=%s",
            zone_entity_id,
            self.coordinator.ac_power_before,
        )
        # Enhanced logging for learning start
        log_fn = cast(
            Callable[[str], Awaitable[None]] | None,
            getattr(self.coordinator, "_log", None),
        )
        if log_fn:
            try:
                await log_fn(
                    f"[LEARNING_START] zone={zone_entity_id} "
                    f"ac_before={round(self.coordinator.ac_power_before or 0, 2)}W "
                    f"mode={getattr(self.coordinator, 'season_mode', 'unknown')}"
                )
            except Exception as exc:
                _LOGGER.exception(
                    "Failed to write learning start to coordinator log: %s",
                    exc,
                )

    async def finish_learning(self) -> LearningResult:
        """Finish learning for the current zone, update learned power, and persist."""
        async with self.session._lock:
            zone = self.session._zone
            if not zone:
                _LOGGER.debug("finish_learning called but no learning zone set")
                return LearningResult(False, error_message="No learning zone set")

            # Use EMA for learning to filter compressor startup surge and stabilize readings.
            # This gives 360+ seconds for transients to settle, resulting in stable learned power values.
            ac_power_now: float | None = None
            ema = getattr(self.coordinator, "ema_30s", None)
            try:
                ac_power_now = float(ema) if ema is not None else None
            except (TypeError, ValueError):
                ac_power_now = None

            if ac_power_now is None:
                _LOGGER.debug(
                    "Unable to read coordinator.ema_30s for learning; aborting"
                )
                await self._reset_learning_state_async()
                return LearningResult(
                    False, error_message="Unable to read EMA for learning"
                )

            ac_before = getattr(self.coordinator, "ac_power_before", None)
            if ac_before is None or ac_power_now is None:
                _LOGGER.debug(
                    "Insufficient data to finish learning (ac_before=%s ac_now=%s)",
                    ac_before,
                    ac_power_now,
                )
                await self._reset_learning_state_async()
                return LearningResult(
                    False, error_message="Insufficient data for learning"
                )

            try:
                delta = abs(float(ac_power_now) - float(ac_before))
            except (ValueError, TypeError):
                _LOGGER.debug(
                    "Failed to compute delta (ac_before=%s ac_now=%s)",
                    ac_before,
                    ac_power_now,
                )
                await self._reset_learning_state_async()
                return LearningResult(
                    False, error_message="Failed to compute power delta"
                )

            zone_name = zone.split(".")[-1]
            zone_state_obj = self.hass.states.get(zone)
            mode = None
            if zone_state_obj:
                hvac_mode = zone_state_obj.attributes.get(
                    "hvac_mode"
                ) or zone_state_obj.attributes.get("hvac_action")
                if isinstance(hvac_mode, str):
                    if "heat" in hvac_mode:
                        mode = "heat"
                    elif "cool" in hvac_mode:
                        mode = "cool"
                else:
                    if zone_state_obj.state == "heat":
                        mode = "heat"
                    elif zone_state_obj.state == "cool":
                        mode = "cool"

            set_lp = getattr(self.coordinator, "set_learned_power", None)
            persist_fn = cast(
                Callable[[], Awaitable[None]] | None,
                getattr(self.coordinator, "async_persist_learned_values", None),
            )
            if not (set_lp and callable(set_lp)) or not persist_fn:
                _LOGGER.error(
                    "Coordinator missing required persistence API; aborting learning save"
                )

                try:
                    await self._reset_learning_state_async()
                except Exception:
                    _LOGGER.exception(
                        "Failed to clear learning state after missing API"
                    )
                return LearningResult(
                    False, error_message="Coordinator missing persistence API"
                )

            try:
                set_lp(zone_name, float(delta), mode=mode)
                self.coordinator.samples = (
                    int(getattr(self.coordinator, "samples", 0) or 0) + 1
                )
                persist_fn = cast(
                    Callable[[], Awaitable[None]] | None,
                    getattr(self.coordinator, "async_persist_learned_values", None),
                )
                if persist_fn:
                    await persist_fn()
                _LOGGER.info(
                    "Finished learning: zone=%s mode=%s delta=%s samples=%s",
                    zone,
                    mode or "default",
                    round(delta, 2),
                    self.coordinator.samples,
                )
                # Enhanced logging for learning completion
                log_fn = cast(
                    Callable[[str], Awaitable[None]] | None,
                    getattr(self.coordinator, "_log", None),
                )
                if log_fn:
                    try:
                        await log_fn(
                            f"[LEARNING_COMPLETE] zone={zone} mode={mode or 'default'} "
                            f"ac_before={round(ac_before, 2)}W ac_after={round(ac_power_now, 2)}W "
                            f"delta={round(delta, 2)}W samples={self.coordinator.samples}"
                        )
                    except Exception as exc2:
                        _LOGGER.exception(
                            "Failed to write learning completion to coordinator log: %s",
                            exc2,
                        )
                await self.session.end_session()
                return LearningResult(True, delta)
            except Exception as exc:
                _LOGGER.exception("Error finishing learning for %s: %s", zone, exc)
                log_fn = cast(
                    Callable[[str], Awaitable[None]] | None,
                    getattr(self.coordinator, "_log", None),
                )
                if log_fn:
                    try:
                        await log_fn(f"[LEARNING_SAVE_ERROR] zone={zone} err={exc}")
                    except Exception as exc2:
                        _LOGGER.exception(
                            "Failed to write learning error to coordinator log: %s",
                            exc2,
                        )
                await self._reset_learning_state_async()
                return LearningResult(False, error_message=str(exc))

    async def reset_learning(self, zone: str | None = None) -> None:
        """Reset learned power values for a specific zone or all zones."""
        if zone:
            # Reset learning for a specific zone
            if zone in self.coordinator.learned_power:
                del self.coordinator.learned_power[zone]
                _LOGGER.info("Controller: reset learning for zone %s", zone)
            else:
                _LOGGER.warning("Controller: zone %s not found in learned_power", zone)
        else:
            # Reset learning for all zones
            self.coordinator.learned_power = {}
            _LOGGER.info("Controller: reset learning for all zones")
        persist_fn = cast(
            Callable[[], Awaitable[None]] | None,
            getattr(self.coordinator, "async_persist_learned_values", None),
        )
        if not persist_fn:
            _LOGGER.error(
                "Coordinator missing persistence API; cannot persist reset learning"
            )
            return

        try:
            if persist_fn:
                await persist_fn()
            if zone:
                _LOGGER.info(
                    "Controller: reset learning for zone %s and persisted", zone
                )
            else:
                _LOGGER.info("Controller: reset learning for all zones and persisted")
        except (OSError, StorageError) as exc:
            _LOGGER.exception("Controller: failed to persist reset learning: %s", exc)
            log_fn = cast(
                Callable[[str], Awaitable[None]] | None,
                getattr(self.coordinator, "_log", None),
            )
            if log_fn:
                try:
                    await log_fn(f"[SERVICE_ERROR] reset_learning {exc}")
                except Exception:
                    _LOGGER.exception(
                        "Failed to write service error to coordinator log"
                    )

    async def _save(self) -> None:
        persist_fn = cast(
            Callable[[], Awaitable[None]] | None,
            getattr(self.coordinator, "async_persist_learned_values", None),
        )
        if persist_fn:
            await persist_fn()
        else:
            _LOGGER.error("Coordinator missing persistence API; _save() no-op")

    def _reset_learning_state(self) -> None:
        self.coordinator.learning_zone = None
        self.coordinator.learning_start_time = None
        self.coordinator.ac_power_before = None
        # Removed: learning_band
        _LOGGER.debug("Controller: cleared learning state")

    async def _reset_learning_state_async(self) -> None:
        await self.session.end_session()
        self._reset_learning_state()
