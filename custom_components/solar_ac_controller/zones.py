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
        """Return (next_zone, last_zone) based on active and locked zones."""
        next_zone = next(
            (
                z
                for z in self.coordinator.config.get(CONF_ZONES, [])
                if z not in active_zones and not self.is_locked(z)
            ),
            None,
        )

        last_zone = next(
            (z for z in reversed(active_zones) if not self.is_locked(z)),
            None,
        )

        return next_zone, last_zone

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
