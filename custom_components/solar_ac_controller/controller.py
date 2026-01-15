# custom_components/solar_ac_controller/controller.py
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_AC_POWER_SENSOR

_LOGGER = logging.getLogger(__name__)


class SolarACController:
    """Controller helper that encapsulates learning operations."""

    def __init__(self, hass: HomeAssistant, coordinator: Any, store: Any | None = None) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.store = store
        self._lock = asyncio.Lock()

    async def start_learning(self, zone_entity_id: str, ac_power_before: float | None) -> None:
        async with self._lock:
            if getattr(self.coordinator, "learning_active", False):
                _LOGGER.debug(
                    "start_learning called but learning already active for zone=%s",
                    getattr(self.coordinator, "learning_zone", None),
                )
                return

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
        async with self._lock:
            zone = getattr(self.coordinator, "learning_zone", None)
            if not zone:
                _LOGGER.debug("finish_learning called but no learning_zone set")
                return

            cfg = getattr(self.coordinator, "config", None) or {}
            ac_entity_id = cfg.get(CONF_AC_POWER_SENSOR) or cfg.get("ac_power_sensor") or cfg.get("ac_sensor")

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
                await self._reset_learning_state_async()
                return

            try:
                delta = abs(float(ac_power_now) - float(ac_before))
            except Exception:
                _LOGGER.debug("Failed to compute delta (ac_before=%s ac_now=%s)", ac_before, ac_power_now)
                await self._reset_learning_state_async()
                return

            zone_name = zone.split(".")[-1]
            zone_state_obj = self.hass.states.get(zone)
            mode = None
            if zone_state_obj:
                hvac_mode = zone_state_obj.attributes.get("hvac_mode") or zone_state_obj.attributes.get("hvac_action")
                if isinstance(hvac_mode, str):
                    if "heat" in hvac_mode:
                        mode = "heat"
                    elif "cool" in hvac_mode:
                        mode = "cool"
                else:
                    if zone_state_obj.state == "heat":
                        mode = "heat"
                    elif zone_state_obj.state == "cool":
                        mode = "cool"

            set_lp = getattr(self.coordinator, "set_learned_power", None)
            persist = getattr(self.coordinator, "_persist_learned_values", None)
            if not callable(set_lp) or not callable(persist):
                _LOGGER.error("Coordinator missing required persistence API; aborting learning save")
                try:
                    await self._reset_learning_state_async()
                except Exception:
                    _LOGGER.exception("Failed to clear learning state after missing API")
                return

            try:
                set_lp(zone_name, float(delta), mode=mode)
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

            await self._reset_learning_state_async()

    async def reset_learning(self) -> None:
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
        persist = getattr(self.coordinator, "_persist_learned_values", None)
        if callable(persist):
            await persist()
        else:
            _LOGGER.error("Coordinator missing persistence API; _save() no-op")

    def _reset_learning_state(self) -> None:
        self.coordinator.learning_active = False
        self.coordinator.learning_zone = None
        self.coordinator.learning_start_time = None
        self.coordinator.ac_power_before = None
        _LOGGER.debug("Controller: cleared learning state")

    async def _reset_learning_state_async(self) -> None:
        self._reset_learning_state()
        await asyncio.sleep(0)
