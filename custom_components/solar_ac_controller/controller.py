from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_AC_POWER_SENSOR

_LOGGER = logging.getLogger(__name__)


class SolarACController:
    """Controller helper that encapsulates learning operations.

    Improvements applied:
    - More robust AC sensor fallback (tries configured sensor, then EMA 30s, then EMA 5m).
    - Logs when AC sensor config is missing to aid debugging.
    - Clarifying comment that learned delta is in watts (compressor delta).
    - Defensive persistence helper with a single retry and clear logging.
    - Type hints and small defensive checks for maintainability.
    """

    def __init__(self, hass: HomeAssistant, coordinator: Any, store: Any | None = None) -> None:
        self.hass = hass
        self.coordinator = coordinator
        # store is kept for compatibility but persistence should go through coordinator
        self.store = store

    # -------------------------
    # Public async API (awaited by coordinator / services)
    # -------------------------
    async def start_learning(self, zone_entity_id: str, ac_power_before: float | None) -> None:
        """Begin a learning session for a zone.

        Marks the coordinator as actively learning and records the baseline AC
        power reading. This method is async to match coordinator usage but
        performs only in-memory updates.
        """
        # Prevent overlapping learning sessions
        if self.coordinator.learning_active:
            _LOGGER.debug(
                "start_learning called but learning already active for zone=%s",
                self.coordinator.learning_zone,
            )
            return

        # Validate and coerce baseline
        try:
            baseline = float(ac_power_before) if ac_power_before is not None else None
        except (TypeError, ValueError):
            baseline = None
            _LOGGER.debug("start_learning: invalid ac_power_before=%s", ac_power_before)

        self.coordinator.learning_active = True
        self.coordinator.learning_zone = zone_entity_id
        self.coordinator.learning_start_time = dt_util.utcnow().timestamp()
        self.coordinator.ac_power_before = baseline

        _LOGGER.debug(
            "Start learning: zone=%s ac_before=%s",
            zone_entity_id,
            self.coordinator.ac_power_before,
        )

    async def finish_learning(self) -> None:
        """Complete the current learning session.

        Reads the current AC power sensor, computes the delta vs the baseline,
        updates the coordinator's learned_power via set_learned_power, increments
        samples, and persists the new learned values.

        Note: the stored learned value is the compressor delta in watts.
        """
        zone = self.coordinator.learning_zone
        if not zone:
            _LOGGER.debug("finish_learning called but no learning_zone set")
            return

        # Use a single, deterministic config lookup for the AC power sensor
        cfg = self.coordinator.config or {}
        ac_entity_id = cfg.get(CONF_AC_POWER_SENSOR) or cfg.get("ac_power_sensor") or cfg.get("ac_sensor")

        if not ac_entity_id:
            _LOGGER.debug("No AC power sensor configured (checked CONF_AC_POWER_SENSOR and fallbacks)")

        # Read current AC power with robust fallbacks:
        # 1) configured sensor entity state
        # 2) coordinator.ema_30s
        # 3) coordinator.ema_5m
        ac_power_now: Optional[float] = None
        if ac_entity_id:
            st = self.hass.states.get(ac_entity_id)
            if st:
                try:
                    ac_power_now = float(st.state)
                except (ValueError, TypeError):
                    _LOGGER.debug("AC sensor %s returned non-numeric state %s", ac_entity_id, st.state)
            else:
                _LOGGER.debug("Configured AC sensor %s not found in state machine", ac_entity_id)

        # Fallback: use coordinator's EMA 30s, then EMA 5m if available
        if ac_power_now is None:
            ema_30 = getattr(self.coordinator, "ema_30s", None)
            ema_5m = getattr(self.coordinator, "ema_5m", None)
            try:
                ac_power_now = float(ema_30) if ema_30 is not None else None
            except (TypeError, ValueError):
                ac_power_now = None

            if ac_power_now is None and ema_5m is not None:
                try:
                    ac_power_now = float(ema_5m)
                except (TypeError, ValueError):
                    ac_power_now = None

            _LOGGER.debug(
                "AC power sensor unreadable; falling back to coordinator EMAs ema_30s=%s ema_5m=%s resolved=%s",
                ema_30,
                ema_5m,
                ac_power_now,
            )

        ac_before = self.coordinator.ac_power_before
        if ac_before is None or ac_power_now is None:
            _LOGGER.debug(
                "Insufficient data to finish learning (ac_before=%s ac_now=%s)",
                ac_before,
                ac_power_now,
            )
            # Reset learning state but do not persist a learned value
            self._reset_learning_state()
            return

        # Compute absolute delta (compressor delta in watts)
        try:
            delta = abs(float(ac_power_now) - float(ac_before))
        except Exception:
            _LOGGER.debug("Failed to compute delta (ac_before=%s ac_now=%s)", ac_before, ac_power_now)
            self._reset_learning_state()
            return

        # Determine mode for the zone (heat/cool/default)
        zone_name = zone.split(".")[-1]
        zone_state_obj = self.hass.states.get(zone)
        mode: Optional[str] = None
        if zone_state_obj:
            if zone_state_obj.state == "heat":
                mode = "heat"
            elif zone_state_obj.state == "cool":
                mode = "cool"

        # Update coordinator learned values and persist with defensive retry
        try:
            # Use coordinator accessor to remain compatible with storage format
            self.coordinator.set_learned_power(zone_name, float(delta), mode=mode)
            # Increment samples (bootstrap/learning sample)
            self.coordinator.samples = int(self.coordinator.samples or 0) + 1
            await self._safe_persist_learned_values()
            _LOGGER.info(
                "Finished learning: zone=%s mode=%s delta=%s samples=%s",
                zone,
                mode or "default",
                round(delta, 2),
                self.coordinator.samples,
            )
        except Exception as exc:
            _LOGGER.exception("Error finishing learning for %s: %s", zone, exc)
            try:
                await self.coordinator._log(f"[LEARNING_SAVE_ERROR] zone={zone} err={exc}")
            except Exception:
                _LOGGER.exception("Failed to write learning error to coordinator log")

        # Clear learning state
        self._reset_learning_state()

    async def reset_learning(self) -> None:
        """Reset all learned values and persist an empty structure.

        This is intended to be called from the integration service handler.
        """
        self.coordinator.learned_power = {}
        self.coordinator.samples = 0
        try:
            await self._safe_persist_learned_values()
            _LOGGER.info("Controller: reset learning and persisted empty learned_power")
        except Exception as exc:
            _LOGGER.exception("Controller: failed to persist reset learning: %s", exc)
            try:
                await self.coordinator._log(f"[SERVICE_ERROR] reset_learning {exc}")
            except Exception:
                _LOGGER.exception("Failed to write service error to coordinator log")

    async def _save(self) -> None:
        """Persist learned values (convenience wrapper)."""
        await self._safe_persist_learned_values()

    # -------------------------
    # Synchronous helpers (used by coordinator without await)
    # -------------------------
    def _reset_learning_state(self) -> None:
        """Clear in-memory learning state without persisting."""
        self.coordinator.learning_active = False
        self.coordinator.learning_zone = None
        self.coordinator.learning_start_time = None
        self.coordinator.ac_power_before = None
        _LOGGER.debug("Controller: cleared learning state")

    # -------------------------
    # Async helper for safe cancellation from coordinator
    # -------------------------
    async def _reset_learning_state_async(self) -> None:
        """Async wrapper to clear learning state safely from async contexts.

        Coordinator can await this to ensure learning flags are cleared when
        master switch turns off or when tasks must be cancelled.
        """
        # No heavy work here; keep behavior identical to synchronous reset
        self._reset_learning_state()
        # allow event loop to settle if caller expects immediate cancellation
        await self.hass.async_add_executor_job(lambda: None)

    # -------------------------
    # Persistence helper with retry
    # -------------------------
    async def _safe_persist_learned_values(self) -> None:
        """Persist learned values via coordinator with a single retry on failure.

        This is best-effort: coordinator already handles exceptions, but a
        retry can help transient storage issues (e.g., IO hiccups).
        """
        try:
            await self.coordinator._persist_learned_values()
        except Exception as exc:
            _LOGGER.warning("Initial persist failed, retrying once: %s", exc)
            try:
                # small backoff before retry
                await asyncio.sleep(0.5)
                await self.coordinator._persist_learned_values()
            except Exception as exc2:
                _LOGGER.exception("Persist retry failed: %s", exc2)
                # Bubble up so callers can log or handle as needed
                raise
