# custom_components/solar_ac_controller/controller.py
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Optional, Tuple, cast

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


@dataclass
class LearningResult:
    success: bool
    learned_power: Optional[float] = None
    error_message: Optional[str] = None


class LearningSession:
    """Encapsulates learning state with thread-safe access and smart phase detection."""

    def __init__(self, coordinator: Any = None) -> None:
        self.coordinator = coordinator
        self._lock = asyncio.Lock()
        self._active = False
        self._zone: Optional[str] = None
        self._start_time: Optional[float] = None
        self._samples = 0

        # Smart phase detection
        self._power_readings: List[Tuple[float, float]] = (
            []
        )  # [(timestamp, power), ...]
        self._peak_power = 0.0
        self._peak_detected = False
        self._stabilized_power = 0.0
        self._stabilized_detected = False

        # Learning contamination protection
        self._learning_contaminated = False
        self._contamination_timestamp: Optional[float] = None
        self._zones_added_during_learning: List[str] = []
        self._peak_detection_timestamp: Optional[float] = None
        self._stabilization_timestamp: Optional[float] = None

    async def is_active(self) -> bool:
        async with self._lock:
            return self._active

    async def start_session(self, zone: str, start_time: float) -> None:
        async with self._lock:
            self._active = True
            self._zone = zone
            self._start_time = start_time
            # Reset contamination tracking for new session
            self._learning_contaminated = False
            self._zones_added_during_learning = []

    async def end_session(self) -> None:
        async with self._lock:
            self._active = False
            self._zone = None
            self._start_time = None
            # Reset contamination tracking
            self._learning_contaminated = False
            self._contamination_timestamp = None
            self._zones_added_during_learning = []
            self._peak_detection_timestamp = None

    async def get_zone(self) -> Optional[str]:
        async with self._lock:
            return self._zone

    async def get_start_time(self) -> Optional[float]:
        async with self._lock:
            return self._start_time

    async def notify_zone_added_during_learning(self, zone: str) -> None:
        """Notify that a zone was added while learning is active (potential contamination)."""
        async with self._lock:
            if self._active and zone != self._zone:
                # Different zone added during learning - mark as contaminated
                now = dt_util.utcnow().timestamp()
                if not self._learning_contaminated:
                    # First contamination - record timestamp
                    self._learning_contaminated = True
                    self._contamination_timestamp = now
                self._zones_added_during_learning.append(zone)
                _LOGGER.warning(
                    f"Zone {zone} added during learning of {self._zone} - "
                    f"learning results may be contaminated"
                )
                # Also log to coordinator for logbook visibility
                log_fn = cast(
                    Callable[[str], Awaitable[None]] | None,
                    getattr(self.coordinator, "_log", None),
                )
                if log_fn:
                    try:
                        await log_fn(
                            f"[LEARNING_CONTAMINATION] zone={zone} added during learning of {self._zone}"
                        )
                    except (AttributeError, TypeError, ValueError):
                        pass

    async def is_learning_contaminated(self) -> bool:
        """Check if current learning session has been contaminated by other zone additions."""
        async with self._lock:
            return self._learning_contaminated

    async def is_peak_valid(self) -> bool:
        """Check if peak detection occurred before any contamination."""
        async with self._lock:
            if not self._peak_detected:
                return False
            if not self._learning_contaminated:
                return True
            # Peak is valid if it was detected before contamination
            return (
                self._peak_detection_timestamp is not None
                and self._contamination_timestamp is not None
                and self._peak_detection_timestamp < self._contamination_timestamp
            )

    async def is_stabilization_valid(self) -> bool:
        """Check if stabilization detection occurred before any contamination."""
        async with self._lock:
            if not self._stabilized_detected:
                return False
            if not self._learning_contaminated:
                return True
            # Stabilization is valid if it was detected before contamination
            return (
                self._stabilization_timestamp is not None
                and self._contamination_timestamp is not None
                and self._stabilization_timestamp < self._contamination_timestamp
            )

    async def add_power_reading(self, power: float) -> None:
        """Add a power reading for smart phase detection."""
        async with self._lock:
            if not self._active:
                return

            now = dt_util.utcnow().timestamp()
            self._power_readings.append((now, power))

            # Update peak tracking
            if power > self._peak_power:
                self._peak_power = power
                self._peak_detected = True
                # Record when peak was first detected
                if self._peak_detection_timestamp is None:
                    self._peak_detection_timestamp = now

            # Detect peak (power started declining)
            elif self._peak_detected and len(self._power_readings) >= 3:
                recent = self._power_readings[-3:]
                if (
                    recent[0][1] > recent[1][1] > recent[2][1]  # Declining trend
                    and recent[1][1] < self._peak_power * 0.9
                ):  # Below 90% of peak
                    self._peak_detected = True  # Lock in peak detection

            # Detect stabilization (low variation in recent readings)
            if len(self._power_readings) >= 24:  # 2 minutes at 5s intervals
                recent_readings = [p for _, p in self._power_readings[-24:]]
                avg_power = sum(recent_readings) / len(recent_readings)
                max_variation = max(recent_readings) - min(recent_readings)

                if max_variation / avg_power < 0.05:  # <5% variation
                    self._stabilized_power = avg_power
                    self._stabilized_detected = True
                    # Record when stabilization was first detected
                    if self._stabilization_timestamp is None:
                        self._stabilization_timestamp = now

    async def get_peak_power(self) -> float:
        """Get the detected peak power during startup."""
        async with self._lock:
            return self._peak_power if self._peak_detected else 0.0

    async def get_stabilized_power(self) -> float:
        """Get the stabilized power after startup."""
        async with self._lock:
            return self._stabilized_power if self._stabilized_detected else 0.0

    async def is_phase_detection_complete(self) -> bool:
        """Check if phase detection is complete."""
        async with self._lock:
            return self._peak_detected and self._stabilized_detected

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
        self.session = LearningSession(coordinator)

    async def is_learning_active(self) -> bool:
        """Check if learning is active, with proper locking."""
        return await self.session.is_active()

    @property
    def is_learning(self) -> bool:
        """Synchronous check if learning is active."""
        return self.session._active

    async def start_learning(
        self, zone_entity_id: str, ac_power_before: float | None
    ) -> None:
        """Begin learning for a zone, storing baseline power and initializing phase detection."""
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

        # Initialize phase detection
        await self.session.add_power_reading(baseline or 0.0)

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
            except (AttributeError, TypeError, ValueError) as exc:
                _LOGGER.exception(
                    "Failed to write learning start to coordinator log: %s",
                    exc,
                )

    async def finish_learning(self) -> LearningResult:
        """Finish learning for the current zone using smart phase detection."""
        async with self.session._lock:
            zone = self.session._zone
            if not zone:
                _LOGGER.debug("finish_learning called but no learning zone set")
                return LearningResult(False, error_message="No learning zone set")

            # Check for learning contamination (other zones added during learning)
            if self.session._learning_contaminated:
                contaminated_zones = self.session._zones_added_during_learning
                peak_valid = await self.session.is_peak_valid()
                stabilization_valid = await self.session.is_stabilization_valid()

                if not peak_valid and not stabilization_valid:
                    # Neither peak nor stabilization is valid - discard everything
                    _LOGGER.warning(
                        f"Discarding all learning results for {zone} due to contamination "
                        f"from zones added during learning: {contaminated_zones}"
                    )
                    # Also log to coordinator for logbook visibility
                    log_fn = cast(
                        Callable[[str], Awaitable[None]] | None,
                        getattr(self.coordinator, "_log", None),
                    )
                    if log_fn:
                        try:
                            await log_fn(
                                f"[LEARNING_CONTAMINATION] zone={zone} action=discard_all "
                                f"contaminated_by={contaminated_zones}"
                            )
                        except (AttributeError, TypeError, ValueError):
                            pass
                    await self._reset_learning_state_async()
                    return LearningResult(
                        False,
                        error_message=f"Learning contaminated by zone additions: {contaminated_zones}",
                    )
                elif peak_valid and not stabilization_valid:
                    # Peak is valid but stabilization is contaminated - use peak only
                    _LOGGER.warning(
                        f"Using peak power for {zone} but discarding stabilization due to contamination "
                        f"from zones added during learning: {contaminated_zones}"
                    )
                    # Also log to coordinator for logbook visibility
                    log_fn = cast(
                        Callable[[str], Awaitable[None]] | None,
                        getattr(self.coordinator, "_log", None),
                    )
                    if log_fn:
                        try:
                            await log_fn(
                                f"[LEARNING_CONTAMINATION] zone={zone} action=use_peak_only "
                                f"contaminated_by={contaminated_zones}"
                            )
                        except (AttributeError, TypeError, ValueError):
                            pass
                elif not peak_valid and stabilization_valid:
                    # This shouldn't happen with current logic, but handle it
                    _LOGGER.warning(
                        f"Peak invalid but stabilization valid for {zone} - using stabilization"
                    )
                    # Also log to coordinator for logbook visibility
                    log_fn = cast(
                        Callable[[str], Awaitable[None]] | None,
                        getattr(self.coordinator, "_log", None),
                    )
                    if log_fn:
                        try:
                            await log_fn(
                                f"[LEARNING_CONTAMINATION] zone={zone} action=use_stabilization "
                                f"contaminated_by={contaminated_zones}"
                            )
                        except (AttributeError, TypeError, ValueError):
                            pass

            # Get peak power from smart phase detection
            peak_power = await self.session.get_peak_power()
            stabilized_power = await self.session.get_stabilized_power()

            # Determine which values are valid based on contamination timing
            peak_valid = await self.session.is_peak_valid()
            stabilization_valid = await self.session.is_stabilization_valid()

            # Calculate learned power: average of valid measurements, or use whichever is available
            if (
                peak_valid
                and peak_power > 0
                and stabilization_valid
                and stabilized_power > 0
            ):
                # Both valid: use average for balanced estimate
                learned_power = (peak_power + stabilized_power) / 2
                _LOGGER.debug(
                    f"Using average of peak ({peak_power}W) and stabilized ({stabilized_power}W) = {learned_power}W for {zone}"
                )
            elif peak_valid and peak_power > 0:
                learned_power = peak_power
                _LOGGER.debug(f"Using valid peak power: {learned_power}W for {zone}")
            elif stabilization_valid and stabilized_power > 0:
                learned_power = stabilized_power
                _LOGGER.debug(
                    f"Using valid stabilized power: {learned_power}W for {zone}"
                )
            else:
                learned_power = peak_power if peak_power > 0 else stabilized_power

            if learned_power <= 0:
                # Fallback to EMA if phase detection failed
                ema = getattr(self.coordinator, "ema_30s", None)
                try:
                    learned_power = float(ema) if ema is not None else None
                except (TypeError, ValueError):
                    learned_power = None

            if learned_power is None or learned_power <= 0:
                _LOGGER.debug(
                    "Unable to determine learned power from phase detection or EMA"
                )
                await self._reset_learning_state_async()
                return LearningResult(
                    False, error_message="Unable to determine learned power"
                )

            ac_before = getattr(self.coordinator, "ac_power_before", None)
            if ac_before is None:
                _LOGGER.debug("No baseline power available for learning")
                await self._reset_learning_state_async()
                return LearningResult(
                    False, error_message="No baseline power available"
                )

            # Calculate delta (learned power - baseline)
            try:
                delta = abs(float(learned_power) - float(ac_before))
            except (ValueError, TypeError):
                _LOGGER.debug("Failed to compute power delta")
                await self._reset_learning_state_async()
                return LearningResult(
                    False, error_message="Failed to compute power delta"
                )

            # Smart bounds checking - focus on peak power but allow reasonable ranges
            zone_name = zone.split(".")[-1]

            # Absolute bounds (keep existing protection)
            MIN_W = 200.0  # From const.LEARNING_MIN_POWER_W
            MAX_W = 3000.0  # From const.LEARNING_MAX_POWER_W

            if not (MIN_W <= delta <= MAX_W):
                _LOGGER.debug(
                    "Discarding outlier sample for %s: %sW outside [%s,%s]",
                    zone_name,
                    delta,
                    MIN_W,
                    MAX_W,
                )
                await self._reset_learning_state_async()
                return LearningResult(
                    False, error_message=f"Power delta {delta}W outside valid range"
                )

            # Get zone mode for learning
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

            # Save the learned power
            set_lp = getattr(self.coordinator, "set_learned_power", None)
            persist_fn = cast(
                Callable[[], Awaitable[None]] | None,
                getattr(self.coordinator, "async_persist_learned_values", None),
            )
            if not (set_lp and callable(set_lp)) or not persist_fn:
                _LOGGER.error(
                    "Coordinator missing required persistence API; aborting learning save"
                )
                await self._reset_learning_state_async()
                return LearningResult(
                    False, error_message="Coordinator missing persistence API"
                )

            try:
                set_lp(zone_name, float(delta), mode=mode)
                self.coordinator.samples = (
                    int(getattr(self.coordinator, "samples", 0) or 0) + 1
                )
                await persist_fn()
                _LOGGER.info(
                    "Finished learning: zone=%s mode=%s delta=%s samples=%s (peak power: %sW)",
                    zone,
                    mode or "default",
                    round(delta, 2),
                    self.coordinator.samples,
                    round(peak_power, 2) if peak_power > 0 else "N/A",
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
                            f"ac_before={round(ac_before, 2)}W learned_power={round(learned_power, 2)}W "
                            f"delta={round(delta, 2)}W samples={self.coordinator.samples} "
                            f"peak_power={round(peak_power, 2) if peak_power > 0 else 'N/A'}W"
                        )
                    except (AttributeError, TypeError, ValueError) as exc2:
                        _LOGGER.exception(
                            "Failed to write learning completion to coordinator log: %s",
                            exc2,
                        )
                await self.session.end_session()
                return LearningResult(True, delta)
            except (ValueError, TypeError, AttributeError, KeyError) as exc:
                _LOGGER.exception("Error finishing learning for %s: %s", zone, exc)
                log_fn = cast(
                    Callable[[str], Awaitable[None]] | None,
                    getattr(self.coordinator, "_log", None),
                )
                if log_fn:
                    try:
                        await log_fn(f"[LEARNING_SAVE_ERROR] zone={zone} err={exc}")
                    except (AttributeError, TypeError, ValueError) as exc2:
                        _LOGGER.exception(
                            "Failed to write learning error to coordinator log: %s",
                            exc2,
                        )
                await self._reset_learning_state_async()
                return LearningResult(False, error_message=str(exc))

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
