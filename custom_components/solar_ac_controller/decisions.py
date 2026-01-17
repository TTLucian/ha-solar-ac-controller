"""Confidence and add/remove decision logic for Solar AC Controller."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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

        export_margin = export - required_export

        base = min(40, max(0, export_margin / 25))
        sample_bonus = min(20, self.coordinator.samples * 2)
        short_cycle_penalty = (
            -30
            if self._is_short_cycling_for_add(last_zone)
            else 0
        )

        return base + 5 + sample_bonus + short_cycle_penalty

    def compute_remove_conf(
        self,
        import_power: float,
        last_zone: str | None,
    ) -> float:
        """Compute remove zone confidence score."""
        base = min(60, max(0, (import_power - 200) / 8))
        heavy_import_bonus = 20 if import_power > 1500 else 0
        short_cycle_penalty = (
            -40
            if self._is_short_cycling_for_remove(last_zone)
            else 0
        )

        return base + 5 + heavy_import_bonus + short_cycle_penalty

    def should_add_zone(
        self, next_zone: str, required_export: float | None
    ) -> bool:
        """Return True if add zone conditions are met."""
        if self.coordinator.learning_active:
            return False

        if self.coordinator.ema_5m > -200:
            return False

        return self.coordinator.last_add_conf >= self.coordinator.add_confidence_threshold

    def should_remove_zone(self, last_zone: str, import_power: float, active_zones: list[str]) -> bool:
        """
        Return True if remove zone conditions are met.
        
        Checks confidence first, then verifies all active zones have reached
        their comfort targets before allowing removal.
        """
        if self.coordinator.last_remove_conf < self.coordinator.remove_confidence_threshold:
            return False
        
        # Block removal if any active zone hasn't reached its comfort target
        if not self.coordinator._all_active_zones_at_target(active_zones):
            return False
        
        return True

    def _is_short_cycling_for_add(self, zone: str | None) -> bool:
        """Check if zone is short-cycling (for add penalty)."""
        if not zone:
            return False
        last = self.coordinator.zone_last_changed.get(zone)
        if not last:
            return False
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
