from __future__ import annotations

import logging
from homeassistant.util import dt as dt_util
from homeassistant.core import HomeAssistant

from .const import CONF_AC_POWER_SENSOR

_LOGGER = logging.getLogger(__name__)


class SolarACController:
    """Learning engine + state transitions for Solar AC."""

    def __init__(self, hass: HomeAssistant, coordinator, store):
        self.hass = hass
        self.coordinator = coordinator
        self.store = store

    # -------------------------------------------------------------------------
    # LEARNING START
    # -------------------------------------------------------------------------
    async def start_learning(self, zone: str, ac_power_before: float):
        """Mark learning as active and store initial state."""
        c = self.coordinator

        c.learning_active = True
        c.learning_start_time = dt_util.utcnow().timestamp()
        c.ac_power_before = ac_power_before
        c.learning_zone = zone

    # -------------------------------------------------------------------------
    # LEARNING FINISH
    # -------------------------------------------------------------------------
    async def finish_learning(self):
        """Finish learning after stabilization delay."""
        c = self.coordinator

        # Guards
        if not c.learning_active:
            return
        if not c.learning_zone:
            return

        zone_entity = c.learning_zone
        zone_state = self.hass.states.get(zone_entity)

        # Determine mode: prefer explicit heat/cool, otherwise use default
        mode = None
        if zone_state and zone_state.state == "heat":
            mode = "heat"
        elif zone_state and zone_state.state == "cool":
            mode = "cool"
        else:
            mode = "default"

        # Abort if zone was manually turned off or changed mode to something unexpected
        if not zone_state or zone_state.state not in ("heat", "cool", "on"):
            await c._log(
                f"[LEARNING_ABORT_MANUAL_INTERVENTION] zone={zone_entity} "
                f"state={zone_state.state if zone_state else 'unknown'}"
            )
            self._reset_learning_state()
            return

        # Abort if zone is locked due to manual override
        lock_until = c.zone_manual_lock_until.get(zone_entity)
        if lock_until and dt_util.utcnow().timestamp() < lock_until:
            await c._log(
                f"[LEARNING_ABORT_MANUAL_LOCK] zone={zone_entity} "
                f"lock_until={int(lock_until)}"
            )
            self._reset_learning_state()
            return

        # Read AC power
        ac_state = self.hass.states.get(c.config.get(CONF_AC_POWER_SENSOR))
        if not ac_state:
            await c._log("[LEARNING_ABORT] ac_power_sensor state missing")
            self._reset_learning_state()
            return

        if ac_state.state in ("unknown", "unavailable"):
            await c._log("[LEARNING_ABORT] ac_power_sensor unavailable")
            self._reset_learning_state()
            return

        try:
            ac_power_now = float(ac_state.state)
        except (ValueError, TypeError):
            await c._log("[LEARNING_ABORT] ac_power_sensor non-numeric")
            self._reset_learning_state()
            return

        delta = ac_power_now - (c.ac_power_before or 0.0)
        zone_name = c.learning_zone.split(".")[-1]

        # ---------------------------------------------------------------------
        # BOOTSTRAP LEARNING (samples == 0)
        # ---------------------------------------------------------------------
        if c.samples == 0:
            min_d = 80
            max_d = 2500

            if min_d < delta < max_d:
                prev = c.get_learned_power(zone_name, mode=mode)
                new_value = round(delta)

                # Store per-mode via coordinator helper
                c.set_learned_power(zone_name, new_value, mode=mode)
                c.samples = 1

                await c._log(
                    f"[LEARNING_BOOTSTRAP] zone={zone_name} mode={mode} delta={round(delta)} "
                    f"prev={prev} new={new_value} samples={c.samples}"
                )

                # Persist via coordinator
                try:
                    await c._persist_learned_values()
                except Exception as exc:
                    _LOGGER.exception("Error saving learned values after bootstrap: %s", exc)
                    try:
                        await c._log(f"[STORAGE_ERROR] {exc}")
                    except Exception:
                        _LOGGER.exception("Failed to write storage error to coordinator log")

                self._reset_learning_state()
                return

            else:
                await c._log(
                    f"[LEARNING_SKIP_BOOTSTRAP] zone={zone_name} mode={mode} delta={round(delta)} "
                    f"expected_range=[{min_d},{max_d}]"
                )
                self._reset_learning_state()
                return

        # ---------------------------------------------------------------------
        # NORMAL LEARNING (samples >= 1)
        # ---------------------------------------------------------------------
        min_d = 250
        max_d = 2500

        if min_d < delta < max_d:
            prev = c.get_learned_power(zone_name, mode=mode)

            # EMA-style update
            alpha = 0.3 if c.samples < 10 else 0.2
            new_value = round(prev * (1 - alpha) + delta * alpha)

            # Store per-mode via coordinator helper
            c.set_learned_power(zone_name, new_value, mode=mode)
            c.samples += 1

            await c._log(
                f"[LEARNING_FINISHED] zone={zone_name} mode={mode} delta={round(delta)} "
                f"prev={prev} new={new_value} samples={c.samples}"
            )

            # Persist via coordinator
            try:
                await c._persist_learned_values()
            except Exception as exc:
                _LOGGER.exception("Error saving learned values after finish: %s", exc)
                try:
                    await c._log(f"[STORAGE_ERROR] {exc}")
                except Exception:
                    _LOGGER.exception("Failed to write storage error to coordinator log")

        else:
            await c._log(
                f"[LEARNING_SKIP] zone={zone_name} mode={mode} delta={round(delta)} "
                f"expected_range=[{min_d},{max_d}]"
            )

        self._reset_learning_state()

    # -------------------------------------------------------------------------
    # RESET LEARNING STATE
    # -------------------------------------------------------------------------
    def _reset_learning_state(self):
        c = self.coordinator
        c.learning_active = False
        c.learning_zone = None
        c.learning_start_time = None
        c.ac_power_before = None

    # -------------------------------------------------------------------------
    # SAVE LEARNED VALUES (deprecated here; coordinator persists)
    # -------------------------------------------------------------------------
    async def _save(self):
        """Backward-compatible save wrapper that delegates to coordinator persistence."""
        try:
            await self.coordinator._persist_learned_values()
        except Exception as e:
            _LOGGER.exception("Error delegating save to coordinator: %s", e)
            try:
                await self.coordinator._log(f"[STORAGE_ERROR] {e}")
            except Exception:
                _LOGGER.exception("Failed to write storage error to coordinator log")

    # -------------------------------------------------------------------------
    # RESET ALL LEARNING
    # -------------------------------------------------------------------------
    async def reset_learning(self):
        """Reset all learned values."""
        c = self.coordinator

        for zone in c.config.get("zones", []):
            zone_name = zone.split(".")[-1]
            # Use coordinator helper to set per-mode defaults
            c.set_learned_power(zone_name, float(c.initial_learned_power), mode=None)

        c.samples = 0

        await c._log("[LEARNING_RESET] all_zones")
        try:
            await c._persist_learned_values()
        except Exception as exc:
            _LOGGER.exception("Error saving learned values after reset: %s", exc)
            try:
                await c._log(f"[STORAGE_ERROR] {exc}")
            except Exception:
                _LOGGER.exception("Failed to write storage error to coordinator log")
