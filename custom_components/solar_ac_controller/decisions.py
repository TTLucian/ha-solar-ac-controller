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
    DECISION_HEAVY_IMPORT_HEADROOM_W,
    DECISION_IMPORT_TOLERANCE_MAX_W,
    DECISION_LEARN_PENALTY_MAG,
    DECISION_RAW_MAX,
    DECISION_RAW_MIN,
    DECISION_REMOVE_BASE_MAX,
    DECISION_SAMPLE_BONUS_MAX,
    DECISION_SAMPLE_BONUS_MULTIPLIER,
    DECISION_SAMPLE_BONUS_RAMP_W,
    DECISION_SHORT_CYCLE_PENALTY_ADD,
    DECISION_SHORT_CYCLE_PENALTY_REMOVE,
    DECISION_STABILITY_DENOM_MIN,
    DECISION_SWAP_BUFFER_W,
    DECISION_VARIABILITY_DIVISOR,
    SOLAR_CLOUD_ADD_PENALTY_MAG,
    SOLAR_FRACTION_ADD_BONUS_MAX,
    SOLAR_FRACTION_BONUS_THRESHOLD,
    SOLAR_FRACTION_REMOVE_SUPPRESS_MAX,
    SOLAR_SLOPE_CLOUD_THRESHOLD_W,
    SOLAR_STABLE_THRESHOLD_W,
    SOLAR_TRANSIENT_IMPORT_CEILING_W,
    SOLAR_TRANSIENT_REMOVE_SUPPRESS_MAG,
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
        if getattr(self.coordinator, "samples", 0) > 0:
            raw_bonus = self.coordinator.samples * DECISION_SAMPLE_BONUS_MULTIPLIER
            # Soft ramp: full bonus at export_margin >= 0, fades to zero at -RAMP_W
            sample_factor = max(
                0.0, min(1.0, 1.0 + export_margin / DECISION_SAMPLE_BONUS_RAMP_W)
            )
            sample_bonus = (
                min(DECISION_SAMPLE_BONUS_MAX, raw_bonus) * bonus_scale * sample_factor
            )

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

        # Cloud detection: solar fast EMA has dropped well below slow EMA → penalise adds.
        # A negative solar_slope means production is contracting (cloud shadow approaching).
        # The penalty scales from 0 at the threshold to the full magnitude one threshold deeper.
        cloud_penalty = 0.0
        try:
            solar_fast = getattr(self.coordinator, "solar_ema_fast", 0.0)
            solar_slow = getattr(self.coordinator, "solar_ema_slow", 0.0)
            solar_slope = solar_fast - solar_slow  # negative = solar dropping
            if solar_slope < -SOLAR_SLOPE_CLOUD_THRESHOLD_W:
                depth = min(
                    1.0,
                    (-solar_slope - SOLAR_SLOPE_CLOUD_THRESHOLD_W)
                    / SOLAR_SLOPE_CLOUD_THRESHOLD_W,
                )
                cloud_penalty = -SOLAR_CLOUD_ADD_PENALTY_MAG * depth * penalty_scale
        except Exception as exc:  # pragma: no cover
            _LOGGER.debug("cloud_penalty calculation failed: %s", exc)
            cloud_penalty = 0.0

        # PV-fraction bonus: when solar is running at a high fraction of rated capacity
        # (peak sun hour) the system can be more aggressive about adding zones.
        # Only active when pv_capacity_w > 0 is configured.
        solar_fraction_bonus = 0.0
        try:
            fraction = getattr(self.coordinator, "solar_fraction", 0.0)
            if fraction > SOLAR_FRACTION_BONUS_THRESHOLD:
                depth_f = (fraction - SOLAR_FRACTION_BONUS_THRESHOLD) / (
                    1.0 - SOLAR_FRACTION_BONUS_THRESHOLD
                )
                solar_fraction_bonus = (
                    SOLAR_FRACTION_ADD_BONUS_MAX * depth_f * bonus_scale
                )
        except Exception as exc:  # pragma: no cover
            _LOGGER.debug("solar_fraction_bonus calculation failed: %s", exc)
            solar_fraction_bonus = 0.0

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
            + cloud_penalty
            + solar_fraction_bonus
        )

        # Debug: log component breakdown to help tuning
        try:
            _LOGGER.debug(
                "[ADD_CONF] zone=%s bs=%.2f ps=%.2f base=%s sample=%s ema=%s stab=%s sc_pen=%s comp_pen=%s learn_pen=%s cloud_pen=%s frac_bonus=%s raw=%s",
                last_zone,
                bonus_scale,
                penalty_scale,
                round(base, 2),
                round(sample_bonus, 2),
                round(ema_bonus, 2),
                round(ac_stability_bonus, 2),
                round(short_cycle_penalty, 2),
                round(comp_penalty, 2),
                round(learn_penalty, 2),
                round(cloud_penalty, 2),
                round(solar_fraction_bonus, 2),
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
                "cloud_penalty": round(cloud_penalty, 2),
                "solar_fraction_bonus": round(solar_fraction_bonus, 2),
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

        # Use import_tolerance as the dead zone: base pressure builds only once
        # ema_5m exceeds what the add-path is willing to tolerate.
        # a=0 → 0 W dead zone, a=0.5 → 350 W, a=1.0 → 700 W
        import_tolerance = float(a) * DECISION_IMPORT_TOLERANCE_MAX_W
        base = min(
            DECISION_REMOVE_BASE_MAX * bonus_scale,
            max(
                0.0,
                (import_power - import_tolerance) / (import_div or 1.0),
            ),
        )
        # Heavy-import threshold is tied to the add-path import tolerance so the
        # two never fight each other: bonus fires only when import exceeds the
        # level at which we'd still consider adding a zone.
        # threshold = (a × 700) + 350  →  a=0: 350W, a=0.5: 700W, a=1.0: 1050W
        heavy_import_threshold = (
            float(a) * DECISION_IMPORT_TOLERANCE_MAX_W
            + DECISION_HEAVY_IMPORT_HEADROOM_W
        )
        heavy_import_bonus = (
            DECISION_HEAVY_IMPORT_BONUS * bonus_scale
            if import_power > heavy_import_threshold
            else 0.0
        )
        short_cycle_penalty = (
            DECISION_SHORT_CYCLE_PENALTY_REMOVE * penalty_scale
            if self._is_short_cycling_for_remove(last_zone)
            else 0.0
        )

        # Transient load-spike suppression: when solar production is stable but the
        # grid briefly goes to import, a household load spike (kettle, oven, EV charge
        # burst) is the most likely cause — not a cloud.  Removing a zone for a 30-second
        # kettle cycle wastes learning history and comfort.  Suppress remove confidence
        # when the solar slope is near zero (solar stable) to give the load time to clear.
        transient_suppress = 0.0
        try:
            solar_fast = getattr(self.coordinator, "solar_ema_fast", 0.0)
            solar_slow = getattr(self.coordinator, "solar_ema_slow", 0.0)
            solar_slope = solar_fast - solar_slow
            # Only suppress when solar is stable AND import is small enough that a
            # household transient (kettle, oven burst) is the plausible cause.
            # Above SOLAR_TRANSIENT_IMPORT_CEILING_W the load is sustained, not a spike.
            if (
                abs(solar_slope) < SOLAR_STABLE_THRESHOLD_W
                and 0 < import_power < SOLAR_TRANSIENT_IMPORT_CEILING_W
            ):
                transient_suppress = (
                    -SOLAR_TRANSIENT_REMOVE_SUPPRESS_MAG * penalty_scale
                )
        except Exception as exc:  # pragma: no cover
            _LOGGER.debug("transient_suppress calculation failed: %s", exc)
            transient_suppress = 0.0

        # Import EMA stability bonus: symmetric counterpart to the add-path ema_bonus.
        # When both EMAs show positive import (slow EMA has been dragged positive), the
        # import is confirmed as sustained rather than a transient.  Reward remove_conf
        # proportionally to how closely the two EMAs agree (stability score).
        import_ema_bonus = 0.0
        try:
            ema_fast = getattr(self.coordinator, "ema_30s", 0.0)
            ema_slow = getattr(self.coordinator, "ema_5m", 0.0)
            if ema_fast > 0 and ema_slow > 0:
                stab_denom = max(DECISION_STABILITY_DENOM_MIN, abs(ema_slow))
                stability_score = max(
                    0.0, 1.0 - (abs(ema_fast - ema_slow) / (stab_denom + 1e-6))
                )
                import_ema_bonus = (
                    stability_score * DECISION_EMA_BONUS_MULTIPLIER * bonus_scale
                )
        except Exception as exc:  # pragma: no cover
            _LOGGER.debug("import_ema_bonus calculation failed: %s", exc)
            import_ema_bonus = 0.0

        # PV-fraction suppression: when solar is at a high fraction of rated capacity
        # (peak sun) any grid import is almost certainly a transient household load, not
        # a sustained solar shortfall.  Only active when pv_capacity_w > 0.
        solar_fraction_suppress = 0.0
        try:
            fraction = getattr(self.coordinator, "solar_fraction", 0.0)
            if fraction > SOLAR_FRACTION_BONUS_THRESHOLD:
                depth_f = (fraction - SOLAR_FRACTION_BONUS_THRESHOLD) / (
                    1.0 - SOLAR_FRACTION_BONUS_THRESHOLD
                )
                solar_fraction_suppress = (
                    -SOLAR_FRACTION_REMOVE_SUPPRESS_MAX * depth_f * penalty_scale
                )
        except Exception as exc:  # pragma: no cover
            _LOGGER.debug("solar_fraction_suppress calculation failed: %s", exc)
            solar_fraction_suppress = 0.0

        raw = (
            base
            + DECISION_CONFIDENCE_OFFSET * bonus_scale
            + heavy_import_bonus
            + import_ema_bonus
            + short_cycle_penalty
            + transient_suppress
            + solar_fraction_suppress
        )
        try:
            self.coordinator.last_remove_breakdown = {
                "import_divisor": round(import_div, 2),
                "heavy_import_threshold": round(heavy_import_threshold, 0),
                "base": round(base, 2),
                "heavy_import_bonus": round(heavy_import_bonus, 2),
                "import_ema_bonus": round(import_ema_bonus, 2),
                "short_cycle_penalty": round(short_cycle_penalty, 2),
                "transient_suppress": round(transient_suppress, 2),
                "solar_fraction_suppress": round(solar_fraction_suppress, 2),
                "raw": round(raw, 2),
            }
        except Exception as exc:  # pragma: no cover
            _LOGGER.debug("Failed to store remove breakdown: %s", exc)
        try:
            _LOGGER.debug(
                "[REM_CONF] zone=%s bs=%.2f ps=%.2f import=%s heavy_thr=%s base=%s heavy=%s ema=%s sc_pen=%s transient_sup=%s frac_sup=%s raw=%s",
                last_zone,
                bonus_scale,
                penalty_scale,
                round(import_power, 2),
                round(heavy_import_threshold, 0),
                round(base, 2),
                round(heavy_import_bonus, 2),
                round(import_ema_bonus, 2),
                round(short_cycle_penalty, 2),
                round(transient_suppress, 2),
                round(solar_fraction_suppress, 2),
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

        Two swap triggers:
        1. Comfort swap: active zone reached its target temperature (10min EMA stable).
           Any higher-priority needy zone can replace it.
        2. Priority-inversion swap: a higher-priority zone needs heating but isn't active,
           while a lower-priority zone is running. Swap them regardless of whether the
           active zone has reached comfort — ensures high-priority zones are not
           permanently locked out when solar surplus is insufficient for a full add.
        """
        # Only swap when comfort-based control is enabled
        if not self.coordinator.enable_temp_modulation:
            return None

        # Never swap while a power-learning session is in progress — adding or
        # removing a zone would contaminate the measurement and force a discard.
        if getattr(self.coordinator, "learning_active_cached", False):
            return None

        # Only swap when confidence is in balanced range (won't add or remove zones)
        # This allows optimization without changing net zone count
        if not (
            self.coordinator.unified_remove_threshold
            < self.coordinator.confidence
            < self.coordinator.unified_add_threshold
        ):
            return None

        active_zones = self.coordinator.active_zones
        available_zones = [
            z
            for z in self.coordinator.config.get(CONF_ZONES, [])
            if z not in active_zones
            and not await self.coordinator.zone_manager.is_locked(z)
        ]

        satisfied_zone_priority = self.coordinator.zone_priorities.get(
            satisfied_zone.split(".")[-1], 999
        )

        # Trigger 1: comfort swap — satisfied_zone has reached its target temperature.
        satisfied_at_target = self.coordinator.zone_manager.is_zone_at_target_stable(
            satisfied_zone
        )
        if satisfied_at_target:
            for zone in sorted(
                available_zones,
                key=lambda z: self.coordinator.zone_priorities.get(
                    z.split(".")[-1], 999
                ),
            ):
                if self.coordinator.zone_manager.does_zone_need_heating(
                    zone
                ) and self._power_compatible_for_swap(zone):
                    return cast(str, zone)

        # Trigger 2: priority-inversion swap — a higher-priority zone needs heating
        # and the current active zone has lower priority.  Swap them so the more
        # important zone gets runtime even without a solar surplus.
        for zone in sorted(
            available_zones,
            key=lambda z: self.coordinator.zone_priorities.get(z.split(".")[-1], 999),
        ):
            zone_priority = self.coordinator.zone_priorities.get(
                zone.split(".")[-1], 999
            )
            if (
                zone_priority < satisfied_zone_priority
                and self.coordinator.zone_manager.does_zone_need_heating(zone)
                and self._power_compatible_for_swap(zone)
            ):
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
