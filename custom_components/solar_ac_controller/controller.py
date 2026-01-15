from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_AC_POWER_SENSOR

_LOGGER = logging.getLogger(__name__)


class SolarACController:
    """Controller helper that encapsulates learning operations.

    This class is intentionally lightweight: it manipulates the coordinator's
    learning-related fields and persists learned values via the coordinator's
    persistence helper. Keeping the controller separate avoids putting all
    learning logic directly in the coordinator and also breaks circular
    import issues when the coordinator is imported during integration setup.
    """

    def __init__(self, hass: HomeAssistant, coordinator: Any, store: Any | None = None) -> None:
        self.hass = hass
        self.coordinator = coordinator
        # store is kept for compatibility but persistence should go through coordinator
        self.store = store
        # simple lock to avoid concurrent start/finish races
        self._lock = asyncio.Lock()

    # -------------------------
    # Public async API (awaited by coordinator / services)
    # -------------------------
    async def start_learning(self, zone_entity_id: str, ac_power_before: float | None) -> None:
        """Begin a learning session for a zone.

        Marks the coordinator as actively learning and records the baseline AC
        power reading. This method is async to match coordinator usage but
        performs only in-memory updates.
        """
        async with self._lock:
            # Prevent overlapping learning sessions
            if getattr(self.coordinator, "learning_active", False):
                _LOGGER.debug(
                    "start_learning called but learning already active for zone=%s",
                    getattr(self.coordinator, "learning_zone", None),
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
            # store epoch seconds for start time
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
        """
        async with self._lock:
            zone = getattr(self.coordinator, "learning_zone", None)
            if not zone:
                _LOGGER.debug("finish_learning called but no learning_zone set")
                return

            # Use a single, deterministic config lookup for the AC power sensor
            cfg = getattr(self.coordinator, "config", None) or {}
            ac_entity_id = cfg.get(CONF_AC_POWER_SENSOR) or cfg.get("ac_power_sensor") or cfg.get("ac_sensor")

            # Read current AC power (null-safe and robust)
            ac_power_now: float | None = None
            if ac_entity_id:
                st = self.hass.states.get(ac_entity_id)
                if st and st.state not in ("unknown", "unavailable", ""):
                    try:
                        ac_power_now = float(st.state)
                    except (ValueError, TypeError):
                        _LOGGER.debug("AC sensor %s returned non-numeric state %s", ac_entity_id, st.state)
                else:
                    _LOGGER.debug("AC sensor %s state unavailable: %s", ac_entity_id, getattr(st, "state", None))

            # Fallback: use coordinator's EMA if sensor unreadable
            if ac_power_now is None:
                ema = getattr(self.coordinator, "ema_30s", None)
                try:
                    ac_power_now = float(ema) if ema is not None else None
                except (TypeError, ValueError):
                    ac_power_now = None
                _LOGGER.debug("AC power sensor unreadable; falling back to coordinator.ema_30s=%s", ac_power_now)

            ac_before = getattr(self.coordinator, "ac_power_before", None)
            if ac_before is None or ac_power_now is None:
                _LOGGER.debug(
                    "Insufficient data to finish learning (ac_before=%s ac_now=%s)",
                    ac_before,
                    ac_power_now,
                )
                # Reset learning state but do not persist a learned value
                await self._reset_learning_state_async()
                return

            # Compute absolute delta (compressor delta)
            try:
                delta = abs(float(ac_power_now) - float(ac_before))
            except Exception:
                _LOGGER.debug("Failed to compute delta (ac_before=%s ac_now=%s)", ac_before, ac_power_now)
                await self._reset_learning_state_async()
                return

            # Determine mode for the zone (heat/cool/default)
            zone_name = zone.split(".")[-1]
            zone_state_obj = self.hass.states.get(zone)
            mode = None
            if zone_state_obj:
                # Prefer explicit hvac_mode or hvac_action attributes if present
                hvac_mode = zone_state_obj.attributes.get("hvac_mode") or zone_state_obj.attributes.get("hvac_action")
                if isinstance(hvac_mode, str):
                    if "heat" in hvac_mode:
                        mode = "heat"
                    elif "cool" in hvac_mode:
                        mode = "cool"
                else:
                    # Fallback to state string
                    if zone_state_obj.state == "heat":
                        mode = "heat"
                    elif zone_state_obj.state == "cool":
                        mode = "cool"

            # Update coordinator learned values (guarded)
            set_lp = getattr(self.coordinator, "set_learned_power", None)
            persist = getattr(self.coordinator, "_persist_learned_values", None)
            if not callable(set_lp) or not callable(persist):
                _LOGGER.error("Coordinator missing required persistence API; aborting learning save")
                try:
                    # still clear state to avoid stuck learning
                    await self._reset_learning_state_async()
                except Exception:
                    _LOGGER.exception("Failed to clear learning state after missing API")
                return

            try:
                # Use coordinator accessor to remain compatible with storage format
                set_lp(zone_name, float(delta), mode=mode)
                # Increment samples (bootstrap/learning sample)
                self.coordinator.samples = int(getattr(self.coordinator, "samples", 0) or 0) + 1
                await persist()
                _LOGGER.info(
                    "Finished learning: zone=%s mode=%s delta=%s samples=%s",
                    zone,
                    mode or "default",
                    round(delta, 2),
                    self.coordinator.samples,
                )
            except Exception as exc:
                _LOGGER.exception("Error finishing learning for %s: %s", zone, exc)
                log_fn = getattr(self.coordinator, "_log", None)
                if callable(log_fn):
                    try:
                        await log_fn(f"[LEARNING_SAVE_ERROR] zone={zone} err={exc}")
                    except Exception:
                        _LOGGER.exception("Failed to write learning error to coordinator log")

            # Clear learning state
            await self._reset_learning_state_async()

    async def reset_learning(self) -> None:
        """Reset all learned values and persist an empty structure.

        This is intended to be called from the integration service handler.
        """
        self.coordinator.learned_power = {}
        self.coordinator.samples = 0
        persist = getattr(self.coordinator, "_persist_learned_values", None)
        if not callable(persist):
            _LOGGER.error("Coordinator missing persistence API; cannot persist reset learning")
            return

        try:
            await persist()
            _LOGGER.info("Controller: reset learning and persisted empty learned_power")
        except Exception as exc:
            _LOGGER.exception("Controller: failed to persist reset learning: %s", exc)
            log_fn = getattr(self.coordinator, "_log", None)
            if callable(log_fn):
                try:
                    await log_fn(f"[SERVICE_ERROR] reset_learning {exc}")
                except Exception:
                    _LOGGER.exception("Failed to write service error to coordinator log")

    async def _save(self) -> None:
        """Persist learned values (convenience wrapper)."""
        persist = getattr(self.coordinator, "_persist_learned_values", None)
        if callable(persist):
            await persist()
        else:
            _LOGGER.error("Coordinator missing persistence API; _save() no-op")

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
        await asyncio.sleep(0)
