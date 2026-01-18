"""Zone state and guard logic for Solar AC Controller."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

from .const import CONF_ZONES

if TYPE_CHECKING:
    from .coordinator import SolarACCoordinator

_LOGGER = logging.getLogger(__name__)


class ZoneManager:
    """Manages zone state tracking, overrides, locks, and short-cycle protection."""

    def __init__(self, coordinator: SolarACCoordinator) -> None:
        """Initialize zone manager."""
        self.coordinator = coordinator

    async def update_zone_states_and_overrides(self) -> list[str]:
        """Update zone states, detect manual overrides, and return active zones."""
        active_zones: list[str] = []

        for zone in self.coordinator.config.get(CONF_ZONES, []):
            state_obj = self.coordinator.hass.states.get(zone)
            if not state_obj:
                continue

            state = state_obj.state
            last_state = self.coordinator.zone_last_state.get(zone)

            # Manual override detection
            if last_state is not None and last_state != state:
                if not (
                    self.coordinator.last_action
                    and (
                        self.coordinator.last_action.endswith(zone)
                        or self.coordinator.last_action == "panic"
                    )
                ):
                    now_ts = dt_util.utcnow().timestamp()
                    self.coordinator.zone_manual_lock_until[zone] = (
                        now_ts + self.coordinator.manual_lock_seconds
                    )
                    await self.coordinator._log(
                        f"[MANUAL_OVERRIDE] zone={zone} state={state} "
                        f"lock_until={int(self.coordinator.zone_manual_lock_until[zone])}"
                    )

            self.coordinator.zone_last_state[zone] = state

            # Treat heating, cooling and generic "on" as active
            if state in ("heat", "cool", "on"):
                active_zones.append(zone)

        return active_zones

    def is_locked(self, zone_id: str) -> bool:
        """Return True if a zone is locked due to manual override."""
        until = self.coordinator.zone_manual_lock_until.get(zone_id)
        return bool(until and dt_util.utcnow().timestamp() < until)

    def select_next_and_last_zone(
        self, active_zones: list[str]
    ) -> tuple[str | None, str | None]:
        """
        Return (next_zone, last_zone) based on active and locked zones.

        When temperature modulation is enabled and season mode is heat/cool:
        - next_zone: Prioritize by thermal need (coldest in heat, hottest in cool)
        - last_zone: Prioritize by comfort achievement (warmest in heat, coolest in cool)

        Otherwise falls back to config order for add and most-recent for remove.
        """
        all_zones = self.coordinator.config.get(CONF_ZONES, [])

        # Determine if we should use temperature-based prioritization
        use_temp_priority = (
            self.coordinator.enable_temperature_modulation
            and self.coordinator.season_mode in ("heat", "cool")
            and self.coordinator.zone_current_temps
        )

        # Select next zone to add
        if use_temp_priority:
            next_zone = self._select_next_by_temperature(all_zones, active_zones)
        else:
            next_zone = next(
                (
                    z
                    for z in all_zones
                    if z not in active_zones and not self.is_locked(z)
                ),
                None,
            )

        # Select last zone to remove
        if use_temp_priority:
            last_zone = self._select_last_by_temperature(active_zones)
        else:
            last_zone = next(
                (z for z in reversed(active_zones) if not self.is_locked(z)),
                None,
            )

        return next_zone, last_zone

    def _select_next_by_temperature(
        self, all_zones: list[str], active_zones: list[str]
    ) -> str | None:
        """
        Select next zone to add based on thermal need.

        Priority:
        1. Filter out zones already at comfort target
        2. From remaining: sort by thermal need (coldest in heat, hottest in cool)
        3. Zones without sensors fall to end, then use config order
        """
        inactive_unlocked = [
            z for z in all_zones if z not in active_zones and not self.is_locked(z)
        ]

        if not inactive_unlocked:
            return None

        # Filter out zones already at comfort target
        zones_not_at_target = [
            z
            for z in inactive_unlocked
            if not self.coordinator._all_active_zones_at_target(z)
        ]

        # If all zones are at target, fall back to all inactive unlocked (for safety)
        candidate_zones = (
            zones_not_at_target if zones_not_at_target else inactive_unlocked
        )

        # Get temperatures and filter zones with valid temps
        zones_with_temps = [
            (z, self.coordinator.zone_current_temps.get(z)) for z in candidate_zones
        ]

        # Separate zones with and without temps
        valid_temp_zones = [(z, t) for z, t in zones_with_temps if t is not None]
        no_temp_zones = [z for z, t in zones_with_temps if t is None]

        if not valid_temp_zones:
            # No valid temps, fall back to config order
            return candidate_zones[0] if candidate_zones else None

        if self.coordinator.season_mode == "heat":
            # Heat: prioritize coldest (lowest temp = highest need)
            sorted_zones = sorted(valid_temp_zones, key=lambda x: x[1])
        else:  # cool
            # Cool: prioritize hottest (highest temp = highest need)
            sorted_zones = sorted(valid_temp_zones, key=lambda x: x[1], reverse=True)

        # Return highest priority zone, or fall back to first no-temp zone (config order)
        return (
            sorted_zones[0][0]
            if sorted_zones
            else (no_temp_zones[0] if no_temp_zones else None)
        )

    def _select_last_by_temperature(self, active_zones: list[str]) -> str | None:
        """
        Select zone to remove based on comfort achievement.

        Priority:
        1. Sort by comfort achieved (warmest in heat, coolest in cool = lowest need, remove first)
        2. Zones without sensors fall to end, then use most-recent activation
        """
        unlocked = [z for z in active_zones if not self.is_locked(z)]

        if not unlocked:
            return None

        # Get temperatures
        zones_with_temps = [
            (z, self.coordinator.zone_current_temps.get(z)) for z in unlocked
        ]

        # Separate zones with and without temps
        valid_temp_zones = [(z, t) for z, t in zones_with_temps if t is not None]
        no_temp_zones = [z for z, t in zones_with_temps if t is None]

        if not valid_temp_zones:
            # No valid temps, fall back to most recently activated
            return unlocked[-1] if unlocked else None

        if self.coordinator.season_mode == "heat":
            # Heat: remove warmest first (highest temp = lowest need, already comfortable)
            sorted_zones = sorted(valid_temp_zones, key=lambda x: x[1], reverse=True)
        else:  # cool
            # Cool: remove coolest first (lowest temp = lowest need, already comfortable)
            sorted_zones = sorted(valid_temp_zones, key=lambda x: x[1])

        # Return highest priority for removal
        # For no-temp zones, prefer most recently activated ones (later in active_zones list)
        if sorted_zones:
            return sorted_zones[0][0]
        elif no_temp_zones:
            # Most recent = last in active_zones that's in no_temp_zones
            for z in reversed(unlocked):
                if z in no_temp_zones:
                    return z

        return None

    def is_short_cycling(self, zone: str | None) -> bool:
        """Return True if a zone is in short-cycle protection."""
        if not zone:
            return False
        last = self.coordinator.zone_last_changed.get(zone)
        if not last:
            return False
        now = dt_util.utcnow().timestamp()
        last_type = self.coordinator.zone_last_changed_type.get(zone)
        if last_type == "on":
            threshold = self.coordinator.short_cycle_on_seconds
        elif last_type == "off":
            threshold = self.coordinator.short_cycle_off_seconds
        else:
            threshold = self.coordinator.short_cycle_off_seconds

        return (now - last) < threshold
