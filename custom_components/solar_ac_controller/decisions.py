"""Confidence and add/remove decision logic for Solar AC Controller."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from homeassistant.util import dt as dt_util

from .const import (
    CONF_ZONES,
    DECISION_AC_STABILITY_BONUS,
    DECISION_AC_STABILITY_THRESHOLD_W,
    DECISION_ADD_CONFIDENCE_BASE_MAX,
    DECISION_COMP_PENALTY_MAG,
    DECISION_CONFIDENCE_OFFSET,
    DECISION_EMA_BONUS_MULTIPLIER,
    DECISION_FINAL_MAX,
    DECISION_FINAL_MIN,
    DECISION_HEAVY_IMPORT_BONUS,
    DECISION_HEAVY_IMPORT_THRESHOLD,
    DECISION_IMPORT_BASE_OFFSET,
    DECISION_IMPORT_TOLERANCE_MAX_W,
    DECISION_LEARN_PENALTY_MAG,
    DECISION_RAW_MAX,
    DECISION_RAW_MIN,
    DECISION_REMOVE_BASE_MAX,
    DECISION_SAMPLE_BONUS_MAX,
    DECISION_SAMPLE_BONUS_MULTIPLIER,
    DECISION_SHORT_CYCLE_PENALTY_ADD,
    DECISION_SHORT_CYCLE_PENALTY_REMOVE,
    DECISION_STABILITY_DENOM_MIN,
    DECISION_SWAP_BUFFER_W,
    DECISION_VARIABILITY_DIVISOR,
)

if TYPE_CHECKING:
    from .coordinator import SolarACCoordinator

_LOGGER = logging.getLogger(__name__)


class DecisionEngine:
    """Computes confidence scores and decides whether to add/remove zones."""

    def __init__(self, coordinator: SolarACCoordinator) -> None:
        """Initialize decision engine."""
        self.coordinator = coordinator

    def _get_dynamic_weight(self, zone_power: float) -> tuple[float, float]:
        """
        Calculate dynamic divisors based on the 'mass' of the zone.
        a = 0.0 (Conservative) -> High Add Divisor (Heavy), Low Remove Divisor (Reactive)
        a = 1.0 (Aggressive)   -> Low Add Divisor (Light), High Remove Divisor (Tolerant)
        """
        a = self.coordinator.aggressiveness

        # Anchor to the learned power, minimum initial_learned_power to avoid jittery math on small loads
        anchor = max(zone_power, self.coordinator.initial_learned_power)

        # Export Divisor (The 'Add' weight)
        # At a=0.5, 1500W zone -> divisor is 37.5.
        # To hit +50 confidence, you need 1875W of excess solar.
        export_div = (anchor * (1.5 - a)) / 40.0

        # Import Divisor (The 'Remove' weight)
        # At a=0.5, 1500W zone -> divisor is 37.5.
        # To hit -50 confidence (threshold), you need 1875W of import.
        # Note: Panic mode still handles the 'Grid Limit' safety at 6kW.
        import_div = (anchor * (1.0 + a)) / 60.0

        return max(export_div, 10.0), max(import_div, 10.0)

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

        now = dt_util.utcnow().timestamp()

        # Aggressiveness scales: higher -> more aggressive (larger bonuses, smaller penalties/divisors)
        a = self.coordinator.aggressiveness
        penalty_scale = max(0.25, 1.5 - 1.0 * float(a))
        bonus_scale = max(0.5, 0.5 + 1.5 * float(a))

        # Import tolerance derived automatically from aggressiveness:
        # 0 W at a=0 (strict), ~350 W at a=0.5 (moderate), 700 W at a=1.0 (permissive)
        tolerance = float(a) * DECISION_IMPORT_TOLERANCE_MAX_W
        export_margin = export_val - required_export_val + tolerance

        # Get dynamic divisors based on zone power
        export_div, _ = self._get_dynamic_weight(required_export_val)

        # Auto-normalize margin divisor based on short-term variability (EMA spread)
        ema_fast = getattr(self.coordinator, "ema_30s", 0.0)
        ema_slow = getattr(self.coordinator, "ema_5m", 0.0)
        variability = abs(ema_fast - ema_slow)
        variability_factor = 1.0 + min(4.0, variability / DECISION_VARIABILITY_DIVISOR)
        scaled_divisor = export_div * variability_factor

        base = min(
            DECISION_ADD_CONFIDENCE_BASE_MAX * bonus_scale,
            export_margin / (scaled_divisor or 1.0),
        )

        # Sample/history bonus only awarded when recent export margin is positive
        sample_bonus = 0.0
        if export_margin > 0 and getattr(self.coordinator, "samples", 0) > 0:
            raw_bonus = self.coordinator.samples * DECISION_SAMPLE_BONUS_MULTIPLIER
            sample_bonus = min(DECISION_SAMPLE_BONUS_MAX, raw_bonus) * bonus_scale

        # EMA stability bonus: reward when fast EMA close to slow EMA (stable power)
        stab_denom = max(DECISION_STABILITY_DENOM_MIN, abs(ema_slow))
        stability_score = max(
            0.0, 1.0 - (abs(ema_fast - ema_slow) / (stab_denom + 1e-6))
        )
        ema_bonus = stability_score * DECISION_EMA_BONUS_MULTIPLIER * bonus_scale

        # Short-cycle penalty (large negative value when recently toggled)
        short_cycle_penalty = (
            DECISION_SHORT_CYCLE_PENALTY_ADD * penalty_scale
            if self._is_short_cycling_for_add(last_zone)
            else 0.0
        )

        # Compressor recovery penalty (decays linearly until recover_until)
        comp_penalty = 0.0
        try:
            recover_until = float(
                getattr(self.coordinator, "compressor_recover_until", 0) or 0
            )
            ramp = float(getattr(self.coordinator, "compressor_ramp_seconds", 0) or 0)
            if ramp > 0 and recover_until > now:
                frac = max(0.0, min(1.0, (recover_until - now) / ramp))
                comp_penalty = -DECISION_COMP_PENALTY_MAG * frac * penalty_scale
        except Exception as exc:  # pragma: no cover
            _LOGGER.debug("comp_penalty calculation failed: %s", exc)
            comp_penalty = 0.0

        # Learning active: strongly suppress adds while learning is active
        learn_penalty = 0.0
        try:
            if getattr(self.coordinator, "learning_active_cached", False):
                learn_penalty = -DECISION_LEARN_PENALTY_MAG
        except Exception as exc:  # pragma: no cover
            _LOGGER.debug("learn_penalty calculation failed: %s", exc)
            learn_penalty = 0.0

        # AC-power stability early-allow: grant a modest bonus when EMA shows stable, substantial export
        ac_stability_bonus = 0.0
        try:
            variability = abs(ema_fast - ema_slow)
            if (
                variability <= DECISION_AC_STABILITY_THRESHOLD_W
                and abs(ema_slow) > DECISION_AC_STABILITY_THRESHOLD_W
            ):
                ac_stability_bonus = DECISION_AC_STABILITY_BONUS * bonus_scale
        except Exception as exc:  # pragma: no cover
            _LOGGER.debug("ac_stability_bonus calculation failed: %s", exc)
            ac_stability_bonus = 0.0

        raw = (
            DECISION_CONFIDENCE_OFFSET * bonus_scale
            + base
            + sample_bonus
            + ema_bonus
            + ac_stability_bonus
            + short_cycle_penalty
            + comp_penalty
            + learn_penalty
        )

        # Debug: log component breakdown to help tuning
        try:
            _LOGGER.debug(
                "[ADD_CONF] zone=%s base=%s sample=%s ema=%s sc_pen=%s comp_pen=%s learn_pen=%s raw=%s",
                last_zone,
                round(base, 2),
                round(sample_bonus, 2),
                round(ema_bonus, 2),
                round(short_cycle_penalty, 2),
                round(comp_penalty, 2),
                round(learn_penalty, 2),
                round(raw, 2),
            )
        except Exception:
            pass

        # Clamp to sensible range
        raw = max(DECISION_RAW_MIN, min(DECISION_RAW_MAX, raw))
        # Store breakdown for diagnostics
        try:
            self.coordinator.last_add_breakdown = {
                "export_divisor": round(scaled_divisor, 2),
                "base": round(base, 2),
                "sample_bonus": round(sample_bonus, 2),
                "ema_bonus": round(ema_bonus, 2),
                "ac_stability_bonus": round(ac_stability_bonus, 2),
                "short_cycle_penalty": round(short_cycle_penalty, 2),
                "comp_penalty": round(comp_penalty, 2),
                "learn_penalty": round(learn_penalty, 2),
                "raw": round(raw, 2),
            }
        except Exception as exc:  # pragma: no cover
            _LOGGER.debug("Failed to store add breakdown: %s", exc)
        return cast(float, max(DECISION_FINAL_MIN, min(DECISION_FINAL_MAX, raw)))

    def compute_remove_conf(
        self,
        import_power: float,
        last_zone: str | None,
    ) -> float:
        """Compute remove zone confidence score."""
        a = self.coordinator.aggressiveness
        penalty_scale = max(0.25, 1.5 - 1.0 * float(a))
        bonus_scale = max(0.5, 0.5 + 1.5 * float(a))

        # Get zone power for dynamic weighting
        zone_power = 1500.0  # default
        if last_zone:
            zone_short = last_zone.split(".")[-1]
            zone_power = (
                self.coordinator.get_learned_power(
                    zone_short, self.coordinator.season_mode
                )
                or 1500.0
            )

        _, import_div = self._get_dynamic_weight(zone_power)

        base = min(
            DECISION_REMOVE_BASE_MAX * bonus_scale,
            max(
                0.0,
                (import_power - DECISION_IMPORT_BASE_OFFSET) / (import_div or 1.0),
            ),
        )
        heavy_import_bonus = (
            DECISION_HEAVY_IMPORT_BONUS * bonus_scale
            if import_power > DECISION_HEAVY_IMPORT_THRESHOLD
            else 0.0
        )
        short_cycle_penalty = (
            DECISION_SHORT_CYCLE_PENALTY_REMOVE * penalty_scale
            if self._is_short_cycling_for_remove(last_zone)
            else 0.0
        )

        raw = (
            base
            + DECISION_CONFIDENCE_OFFSET * bonus_scale
            + heavy_import_bonus
            + short_cycle_penalty
        )
        try:
            self.coordinator.last_remove_breakdown = {
                "import_divisor": round(import_div, 2),
                "base": round(base, 2),
                "heavy_import_bonus": round(heavy_import_bonus, 2),
                "short_cycle_penalty": round(short_cycle_penalty, 2),
                "raw": round(raw, 2),
            }
        except Exception as exc:  # pragma: no cover
            _LOGGER.debug("Failed to store remove breakdown: %s", exc)
        try:
            _LOGGER.debug(
                "[REM_CONF] zone=%s import=%s base=%s heavy=%s sc_pen=%s raw=%s",
                last_zone,
                round(import_power, 2),
                round(base, 2),
                round(heavy_import_bonus, 2),
                round(short_cycle_penalty, 2),
                round(raw, 2),
            )
        except Exception:
            pass
        return max(0.0, min(100.0, raw))

    async def should_add_zone(
        self, next_zone: str, required_export: float | None
    ) -> bool:
        """Return True if add zone conditions are met using unified confidence only."""
        # Decision is driven by unified confidence computed in coordinator loop.
        # This method simply returns whether the unified confidence meets the add threshold.
        return getattr(self.coordinator, "confidence", 0.0) >= getattr(
            self.coordinator, "unified_add_threshold", 0.0
        )

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

        return bool((now - last) < threshold)

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
        if not self.coordinator.zone_manager.is_zone_at_target_stable(satisfied_zone):
            return None

        # Find highest priority zone that needs heating but isn't active
        active_zones = self.coordinator.active_zones
        available_zones = [
            z
            for z in self.coordinator.config.get(CONF_ZONES, [])
            if z not in active_zones
            and not await self.coordinator.zone_manager.is_locked(z)
        ]

        for zone in sorted(
            available_zones,
            key=lambda z: self.coordinator.zone_priorities.get(z.split(".")[-1], 999),
        ):
            if self.coordinator.zone_manager.does_zone_need_heating(
                zone
            ) and self._power_compatible_for_swap(zone):
                return cast(str, zone)

        return None

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

        return zone_power <= max_active_power + DECISION_SWAP_BUFFER_W
