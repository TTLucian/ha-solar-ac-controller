"""Confidence and add/remove decision logic for Solar AC Controller."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import (
    CONF_ZONES,
    DECISION_ADD_CONFIDENCE_BASE_MAX,
    DECISION_CONFIDENCE_OFFSET,
    DECISION_EXPORT_MARGIN_DIVISOR,
    DECISION_HEAVY_IMPORT_BONUS,
    DECISION_HEAVY_IMPORT_THRESHOLD,
    DECISION_IMPORT_BASE_OFFSET,
    DECISION_IMPORT_DIVISOR,
    DECISION_REMOVE_BASE_MAX,
    DECISION_SAMPLE_BONUS_MAX,
    DECISION_SAMPLE_BONUS_MULTIPLIER,
    DECISION_SHORT_CYCLE_PENALTY_ADD,
    DECISION_SHORT_CYCLE_PENALTY_REMOVE,
    GRID_IMPORT_TOLERANCE_W,
)

if TYPE_CHECKING:
    from .coordinator import SolarACCoordinator

_LOGGER = logging.getLogger(__name__)


class DecisionEngine:
    """Computes confidence scores and decides whether to add/remove zones."""

    def __init__(self, coordinator: SolarACCoordinator) -> None:
        """Initialize decision engine."""
        self.coordinator = coordinator

    def compute_add_conf(
        self,
        export: float,
        required_export: float | None,
        last_zone: str | None,
    ) -> float:
        """Compute add zone confidence score."""
        if required_export is None:
            return 0.0

        # Defensive: ensure numeric
        try:
            export_val = float(export)
            required_export_val = float(required_export)
        except (TypeError, ValueError):
            return 0.0

        export_margin = export_val - required_export_val

        base = min(
            DECISION_ADD_CONFIDENCE_BASE_MAX,
            max(0.0, export_margin / DECISION_EXPORT_MARGIN_DIVISOR),
        )
        sample_bonus = min(
            DECISION_SAMPLE_BONUS_MAX,
            self.coordinator.samples * DECISION_SAMPLE_BONUS_MULTIPLIER,
        )
        short_cycle_penalty = (
            DECISION_SHORT_CYCLE_PENALTY_ADD
            if self._is_short_cycling_for_add(last_zone)
            else 0.0
        )

        return base + DECISION_CONFIDENCE_OFFSET + sample_bonus + short_cycle_penalty

    def compute_remove_conf(
        self,
        import_power: float,
        last_zone: str | None,
    ) -> float:
        """Compute remove zone confidence score.

        PANIC FAST-TRACK: If import_power >= panic_threshold, immediately return 100.0.
        """
        if import_power >= self.coordinator.panic_threshold:
            return 100.0

        base = min(
            DECISION_REMOVE_BASE_MAX,
            max(
                0.0,
                (import_power - DECISION_IMPORT_BASE_OFFSET) / DECISION_IMPORT_DIVISOR,
            ),
        )
        heavy_import_bonus = (
            DECISION_HEAVY_IMPORT_BONUS
            if import_power > DECISION_HEAVY_IMPORT_THRESHOLD
            else 0.0
        )
        short_cycle_penalty = (
            DECISION_SHORT_CYCLE_PENALTY_REMOVE
            if self._is_short_cycling_for_remove(last_zone)
            else 0.0
        )

        return (
            base + DECISION_CONFIDENCE_OFFSET + heavy_import_bonus + short_cycle_penalty
        )

    async def should_add_zone(
        self, next_zone: str, required_export: float | None
    ) -> bool:
        """Return True if add zone conditions are met."""
        if await self.coordinator.controller.is_learning_active():
            return False

        if self.coordinator.ema_5m > -200:
            return False

        # Check if we have sufficient export capacity (with grid import tolerance)
        if required_export is not None:
            current_export = -self.coordinator.ema_30s  # Convert to positive export
            # Allow zone addition if export is sufficient or grid import is within tolerance
            min_required_export = required_export - GRID_IMPORT_TOLERANCE_W
            if current_export < min_required_export:
                return False

        return self.coordinator.confidence >= self.coordinator.unified_add_threshold

    # Multi-zone addition/abundance logic removed — single-zone additions only.

    async def should_remove_zone(
        self, last_zone: str, import_power: float, active_zones: list[str]
    ) -> bool:
        """
        Return True if remove zone conditions are met.

        Only checks remove confidence - comfort targets are ignored to allow
        aggressive zone removal based on power conditions alone.
        """
        if self.coordinator.confidence > self.coordinator.unified_remove_threshold:
            return False

        # Allow removal during panic (emergency override)
        if (
            self.coordinator.panic_manager
            and self.coordinator.panic_manager.is_panicking
        ):
            return True

        # Remove based on confidence alone - no comfort target check
        return True

    def _is_short_cycling_for_add(self, zone: str | None) -> bool:
        """Check if zone is short-cycling (for add penalty).

        Uses time.monotonic() for interval measurement if both now and last are monotonic values.
        If last is a wall-clock timestamp (from dt_util), uses dt_util.utcnow().timestamp().
        """

        if not zone:
            return False
        last = self.coordinator.zone_last_changed.get(zone)
        if not last:
            return False
        # If last is monotonic, use monotonic; else fallback to wall time
        # (Assume all zone_last_changed are wall time for HA compatibility)
        from homeassistant.util import dt as dt_util

        now = dt_util.utcnow().timestamp()
        last_type = self.coordinator.zone_last_changed_type.get(zone)
        if last_type == "on":
            threshold = self.coordinator.short_cycle_on_seconds
        elif last_type == "off":
            threshold = self.coordinator.short_cycle_off_seconds
        else:
            threshold = self.coordinator.short_cycle_off_seconds

        return (now - last) < threshold

    def _is_short_cycling_for_remove(self, zone: str | None) -> bool:
        """Check if zone is short-cycling (for remove penalty)."""
        return self._is_short_cycling_for_add(zone)

    async def should_swap_zone(
        self, satisfied_zone: str, import_power: float
    ) -> str | None:
        """
        Check if we should swap a satisfied zone with a higher-priority needy zone.

        Only active when comfort-based zone control is enabled.
        Returns the zone to add, or None if no swap needed.
        """
        # Only swap when comfort-based control is enabled
        if not self.coordinator.enable_temp_modulation:
            return None

        # Only swap when confidence is in balanced range (won't add or remove zones)
        # This allows optimization without changing net zone count
        if not (
            self.coordinator.unified_remove_threshold
            < self.coordinator.confidence
            < self.coordinator.unified_add_threshold
        ):
            return None

        # Check if satisfied zone actually reached target (using 10min EMA)
        if not self._zone_reached_target_stable(satisfied_zone):
            return None

        # Find highest priority zone that needs heating but isn't active
        active_zones = self.coordinator.active_zones
        available_zones = [
            z
            for z in self.coordinator.config.get(CONF_ZONES, [])
            if z not in active_zones and not self.coordinator.zone_manager.is_locked(z)
        ]

        for zone in sorted(
            available_zones,
            key=lambda z: self.coordinator.zone_priorities.get(z.split(".")[-1], 999),
        ):
            if self._zone_needs_heating(zone) and self._power_compatible_for_swap(zone):
                return zone

        return None

    def _zone_reached_target_stable(self, zone: str) -> bool:
        """Check if zone reached target using stable 10min EMA."""
        ema_temp = self.coordinator.temp_ema_10m.get(zone)
        if ema_temp is None:
            return False

        target = (
            self.coordinator.max_temp_winter
            if self.coordinator.season_mode == "heat"
            else self.coordinator.min_temp_summer
        )
        margin = 0.5  # 0.5°C stability margin

        return (
            (ema_temp >= target - margin)
            if self.coordinator.season_mode == "heat"
            else (ema_temp <= target + margin)
        )

    def _zone_needs_heating(self, zone: str) -> bool:
        """Check if zone needs heating based on current temp vs target."""
        current_temp = self.coordinator.zone_current_temps.get(zone)
        if current_temp is None:
            return False

        return current_temp < self.coordinator.max_temp_winter - 1  # 1°C below target

    def _power_compatible_for_swap(self, zone: str) -> bool:
        """Check if zone's power requirements are compatible for swapping."""
        zone_name = zone.split(".")[-1]
        zone_power = self.coordinator.get_learned_power(
            zone_name, self.coordinator.season_mode
        )

        # For multi-split: first zone typically draws most power
        # Allow swap if new zone power is <= current highest power zone + buffer
        active_zones = self.coordinator.active_zones
        active_powers = [
            self.coordinator.get_learned_power(
                z.split(".")[-1], self.coordinator.season_mode
            )
            for z in active_zones
        ]
        max_active_power = max(active_powers) if active_powers else 0

        return zone_power <= max_active_power + 200  # 200W buffer
