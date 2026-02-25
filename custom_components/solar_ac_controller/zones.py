"""Zone state and guard logic for Solar AC Controller."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from homeassistant.util import dt as dt_util

from .const import COMMAND_CONTEXT_GRACE_SECONDS, CONF_ZONES

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

        # Get all states once for efficient batch lookup
        all_states = self.coordinator.hass.states.async_all()
        state_map = {s.entity_id: s for s in all_states}

        for zone in self.coordinator.config.get(CONF_ZONES, []):
            state_obj = state_map.get(zone)
            if not state_obj:
                _LOGGER.warning(
                    f"Configured zone entity '{zone}' is missing in Home Assistant. Check for typos or missing entities."
                )
                continue

            state = state_obj.state
            last_state = self.coordinator.zone_last_state.get(zone)

            # Manual override detection.
            # A state change is only a manual override if it occurs outside the
            # per-zone coordinator grace window.  zone_last_changed[zone] is
            # updated atomically every time the coordinator issues a command to
            # that zone (add_zone / remove_zone / panic_shed / call_entity_service),
            # so it is immune to last_action drifting to "balanced" or any other
            # unrelated state while a slow climate entity is still catching up.
            if last_state is not None and last_state != state:
                now_ts = dt_util.utcnow().timestamp()
                last_cmd_ts = self.coordinator.zone_last_changed.get(zone, 0.0)

                # --- Authorship detection ---
                # Primary: compare the state-change context ID against the
                # context ID the integration stamped when it issued the command.
                stored_ctx_entry = self.coordinator.zone_last_context_id.get(zone)
                authored_by_integration = False
                if stored_ctx_entry is not None:
                    ctx_id, _issued_ts = stored_ctx_entry
                    authored_by_integration = (
                        state_obj.context.id == ctx_id
                        or getattr(state_obj.context, "parent_id", None) == ctx_id
                    )

                # Fallback: short grace window covers slow/bridged entities where
                # context propagation may not be guaranteed (e.g. Zigbee bridges).
                within_grace = (now_ts - last_cmd_ts) <= COMMAND_CONTEXT_GRACE_SECONDS

                is_panic = self.coordinator.last_action == "panic"

                if not authored_by_integration and not within_grace and not is_panic:
                    self.coordinator.zone_manual_lock_until[zone] = (
                        now_ts + self.coordinator.manual_lock_seconds
                    )
                    self.coordinator._record_zone_action(
                        zone,
                        f"manual_{state}",
                        source="manual",
                        reason="state changed without integration context",
                    )
                    await self.coordinator._log(
                        f"[MANUAL_OVERRIDE] zone={zone} state={state} "
                        f"lock_until={int(self.coordinator.zone_manual_lock_until[zone])}",
                        "warning",
                    )

            self.coordinator.zone_last_state[zone] = state

            # Treat heating, cooling and generic "on" as active
            if state in ("heat", "cool", "on"):
                active_zones.append(zone)

        return active_zones

    async def is_locked(self, zone_id: str) -> bool:
        """Return True if a zone is locked due to manual override."""
        should_log = False

        # Check lock status under protection of state lock
        async with self.coordinator._state_lock:
            until = self.coordinator.zone_manual_lock_until.get(zone_id)
            if until:
                now = dt_util.utcnow().timestamp()
                if now >= until:
                    # Lock has expired - remove it
                    del self.coordinator.zone_manual_lock_until[zone_id]
                    should_log = True
                else:
                    return True
            else:
                return False

        # Log expiration outside the lock to avoid I/O under lock
        if should_log:
            # Schedule expiration log on HA loop using coordinator helper
            self.coordinator.create_background_task(
                self._log_zone_lock_expired(zone_id)
            )

        return False

    async def _log_zone_lock_expired(self, zone_id: str) -> None:
        """Log zone lock expiration event."""
        await self.coordinator._log(
            f"Manual override lock expired for zone '{zone_id}' - zone can now be controlled automatically",
            "info",
        )

    async def select_next_and_last_zone(
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
        next_zone = None
        for z in all_zones:
            if (
                z not in active_zones
                and not await self.is_locked(z)
                and self._is_zone_available(z)
            ):
                next_zone = z
                break

        # Determine if we should use temperature-based removal prioritization
        use_temp_priority = (
            getattr(self.coordinator, "enable_temp_modulation", False)
            and self.coordinator.season_mode in ("heat", "cool")
            and self.coordinator.zone_current_temps
        )

        # Select last zone to remove: by comfort (if temp enabled) or by recency
        if use_temp_priority:
            last_zone = await self._select_last_by_temperature(active_zones)
        else:
            last_zone = None
            for z in reversed(active_zones):
                if not await self.is_locked(z) and self._is_zone_available(z):
                    last_zone = z
                    break

        return next_zone, last_zone

    async def _select_last_by_temperature(self, active_zones: list[str]) -> str | None:
        """
        Select zone to remove based on comfort achievement.

        When a zone reaches its comfort temperature target, it becomes a candidate
        for removal (lowest need). Zones not at target continue running.

        Priority:
        1. Zones at comfort temperature (ready to remove)
        2. Among those at comfort, sort by comfort margin (warmest in heat, coolest in cool)
        3. Zones without sensors treated conservatively (kept on unless no other choice)
        Fallback: If no zones at target and removal is required (e.g., high import), return least important unlocked zone.
        """
        unlocked = []
        for z in active_zones:
            if not await self.is_locked(z) and self._is_zone_available(z):
                unlocked.append(z)

        if not unlocked:
            return None

        # Get temperatures and comfort status
        zones_info = []
        for z in unlocked:
            temp = self.coordinator.zone_current_temps.get(z)
            at_target = self.is_zone_at_target(z)
            zones_info.append((z, temp, at_target))

        # Separate zones by comfort status
        zones_at_target = [z for z, t, at_target in zones_info if at_target]

        # Prioritize removing zones that have reached comfort target
        if zones_at_target:
            candidate_zones = zones_at_target
            # Among removal candidates, sort by comfort margin
            zones_with_temps = [
                (z, self.coordinator.zone_current_temps.get(z)) for z in candidate_zones
            ]
            valid_temp_zones = [(z, t) for z, t in zones_with_temps if t is not None]
            if not valid_temp_zones:
                # No valid temps among at-target zones, pick the oldest activated one
                return candidate_zones[-1] if candidate_zones else None
            if self.coordinator.season_mode == "heat":
                # Heat: remove warmest first (highest temp = most above target)
                sorted_zones = sorted(
                    valid_temp_zones, key=lambda x: x[1], reverse=True
                )
            else:  # cool
                # Cool: remove coolest first (lowest temp = most below target)
                sorted_zones = sorted(valid_temp_zones, key=lambda x: x[1])
            return sorted_zones[0][0] if sorted_zones else None
        else:
            # Fallback: If no zones at target and removal is required (e.g., high import),
            # return the least important unlocked zone (oldest activated)
            # This fallback should only be used if the decision engine is requesting removal
            # due to high import, not for minor solar dips.
            # For now, return the oldest unlocked zone (last in list)
            return unlocked[-1] if unlocked else None

    def is_short_cycling(
        self, zone: str | None, bypass_short_cycle: bool = False
    ) -> bool:
        """Return True if a zone is in short-cycle protection.
        If bypass_short_cycle is True, always return False (for panic/critical situations).
        """
        if bypass_short_cycle:
            return False
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

        return cast(bool, (now - last) < threshold)

    def is_zone_at_target(self, zone: str) -> bool:
        """
        Check if the specified zone has reached its comfort target using current temperature.

        Returns True if the zone is at or above/below target:
        - In heat mode: zone >= max_temp_winter
        - In cool mode: zone <= min_temp_summer

        Returns False if zone has no sensor or is not at target.
        """
        if not zone or not self.coordinator.season_mode:
            return False

        current_temp = self.coordinator.zone_current_temps.get(zone)
        if current_temp is None:
            return False

        if self.coordinator.season_mode == "heat":
            return current_temp >= self.coordinator.max_temp_winter
        elif self.coordinator.season_mode == "cool":
            return current_temp <= self.coordinator.min_temp_summer

        # Unknown/future season_mode: treat zone as NOT at target (conservative –
        # keeps zones running rather than flagging everything for removal).
        return False

    def is_zone_at_target_stable(self, zone: str) -> bool:
        """
        Check if zone reached target using stable 10min EMA temperature.

        Uses a margin for stability to prevent oscillation.
        """
        from .const import DECISION_ZONE_TEMP_MARGIN

        ema_temp = self.coordinator.temp_ema_10m.get(zone)
        if ema_temp is None:
            return False

        if self.coordinator.season_mode == "heat":
            target = self.coordinator.max_temp_winter
            margin = DECISION_ZONE_TEMP_MARGIN
            return ema_temp >= target - margin
        elif self.coordinator.season_mode == "cool":
            target = self.coordinator.min_temp_summer
            margin = DECISION_ZONE_TEMP_MARGIN
            return ema_temp <= target + margin

        # Unknown/future season_mode: conservative fallback – not at target.
        return False

    def does_zone_need_heating(self, zone: str) -> bool:
        """
        Check if zone needs heating based on current temp vs target.

        Only meaningful in heat mode. Returns True if zone temperature
        is significantly below the winter target.
        """
        from .const import DECISION_ZONE_NEEDS_HEATING_DIFF

        if self.coordinator.season_mode != "heat":
            return False

        current_temp = self.coordinator.zone_current_temps.get(zone)
        if current_temp is None:
            return False

        return (
            current_temp
            < self.coordinator.max_temp_winter - DECISION_ZONE_NEEDS_HEATING_DIFF
        )

    def _is_zone_available(self, zone: str) -> bool:
        """Check if a zone entity is available (not unavailable state)."""
        state_obj = self.coordinator.hass.states.get(zone)
        if not state_obj:
            return False
        return cast(str, state_obj.state) != "unavailable"
