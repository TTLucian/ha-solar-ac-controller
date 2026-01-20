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

        Zone activation always follows config order (next_zone = first inactive unlocked).
        When temperature modulation is enabled and season mode is heat/cool:
        - last_zone: Zones at comfort temperature are removed first (lowest need)

        Otherwise fall back to most-recent activation for removal.
        """
        all_zones = self.coordinator.config.get(CONF_ZONES, [])

        # Next zone always uses config order (simplest, most predictable)
        next_zone = next(
            (
                z
                for z in all_zones
                if z not in active_zones and not self.is_locked(z)
            ),
            None,
        )

        # Determine if we should use temperature-based removal prioritization
        use_temp_priority = (
            getattr(self.coordinator, "enable_temp_modulation", False)
            and self.coordinator.season_mode in ("heat", "cool")
            and self.coordinator.zone_current_temps
        )

        # Select last zone to remove: by comfort (if temp enabled) or by recency
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
        DEPRECATED: Zone add no longer uses temperature prioritization.
        Kept for reference only; select_next_and_last_zone now always uses config order.
        """
        # This method is no longer called but kept to avoid breaking imports
        return next(
            (
                z
                for z in all_zones
                if z not in active_zones and not self.is_locked(z)
            ),
            None,
        )

    def _select_last_by_temperature(self, active_zones: list[str]) -> str | None:
        """
        Select zone to remove based on comfort achievement.

        When a zone reaches its comfort temperature target, it becomes a candidate
        for removal (lowest need). Zones not at target continue running.

        Priority:
        1. Zones at comfort temperature (ready to remove)
        2. Among those at comfort, sort by comfort margin (warmest in heat, coolest in cool)
        3. Zones without sensors treated conservatively (kept on unless no other choice)
        """
        unlocked = [z for z in active_zones if not self.is_locked(z)]

        if not unlocked:
            return None

        # Get temperatures and comfort status
        zones_info = []
        for z in unlocked:
            temp = self.coordinator.zone_current_temps.get(z)
            at_target = self.coordinator._all_active_zones_at_target(z)
            zones_info.append((z, temp, at_target))

        # Separate zones by comfort status
        zones_at_target = [z for z, t, at_target in zones_info if at_target]
        zones_not_at_target = [z for z, t, at_target in zones_info if not at_target]

        # Prioritize removing zones that have reached comfort target
        if zones_at_target:
            candidate_zones = zones_at_target
        else:
            # If no zones at target, keep all running (don't remove yet)
            return None

        # Among removal candidates, sort by comfort margin
        zones_with_temps = [
            (z, self.coordinator.zone_current_temps.get(z))
            for z in candidate_zones
        ]

        valid_temp_zones = [(z, t) for z, t in zones_with_temps if t is not None]
        no_temp_zones = [z for z, t in zones_with_temps if t is None]

        if not valid_temp_zones:
            # No valid temps among at-target zones, pick the oldest activated one
            return candidate_zones[-1] if candidate_zones else None

        if self.coordinator.season_mode == "heat":
            # Heat: remove warmest first (highest temp = most above target)
            sorted_zones = sorted(valid_temp_zones, key=lambda x: x[1], reverse=True)
        else:  # cool
            # Cool: remove coolest first (lowest temp = most below target)
            sorted_zones = sorted(valid_temp_zones, key=lambda x: x[1])

        return sorted_zones[0][0] if sorted_zones else None

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
