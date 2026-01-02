from __future__ import annotations

from homeassistant.util import dt as dt_util
from homeassistant.core import HomeAssistant


class SolarACController:
    """Learning engine + state transitions for Solar AC."""

    def __init__(self, hass: HomeAssistant, coordinator, store):
        self.hass = hass
        self.coordinator = coordinator
        self.store = store

    async def start_learning(self, zone, ac_power_before):
        """Mark learning as active and store initial state."""
        c = self.coordinator

        c.learning_active = True
        c.learning_start_time = dt_util.utcnow().timestamp()
        c.ac_power_before = ac_power_before
        c.learning_zone = zone

    async def finish_learning(self):
        """Finish learning after stabilization delay."""
        c = self.coordinator

        # Improvement #9: explicit guards
        if not c.learning_active:
            return
        if not c.learning_zone:
            return

        zone_entity = c.learning_zone
        zone_state = self.hass.states.get(zone_entity)

        # Abort if zone was manually turned off or changed mode
        if not zone_state or zone_state.state not in ("heat", "on"):
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
        ac_state = self.hass.states.get(c.config["ac_power_sensor"])
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
        except ValueError:
            await c._log("[LEARNING_ABORT] ac_power_sensor non-numeric")
            self._reset_learning_state()
            return

        delta = ac_power_now - (c.ac_power_before or 0.0)
        zone_name = c.learning_zone.split(".")[-1]

        # -----------------------------
        # BOOTSTRAP LEARNING (samples == 0)
        # -----------------------------
        if c.samples == 0:
            min_d = 80
            max_d = 2500
            if min_d < delta < max_d:
                prev = c.learned_power.get(zone_name, 1200)
                # First sample = direct measurement (no EMA yet)
                new_value = round(delta)

                c.learned_power[zone_name] = new_value
                c.samples = 1

                await c._log(
                    f"[LEARNING_BOOTSTRAP] zone={zone_name} delta={round(delta)} "
                    f"prev={prev} new={new_value} samples={c.samples}"
                )

                await self._save()
                self._reset_learning_state()
                return
            else:
                await c._log(
                    f"[LEARNING_SKIP_BOOTSTRAP] zone={zone_name} delta={round(delta)} "
                    f"expected_range=[{min_d},{max_d}]"
                )
                self._reset_learning_state()
                return

        # -----------------------------
        # NORMAL LEARNING (samples >= 1)
        # -----------------------------
        min_d = 250
        max_d = 2500
        if min_d < delta < max_d:
            prev = c.learned_power.get(zone_name, 1200)

            # EMA-style update: trust previous value more once we have samples
            alpha = 0.3 if c.samples < 10 else 0.2
            new_value = round(prev * (1 - alpha) + delta * alpha)

            c.learned_power[zone_name] = new_value
            c.samples += 1

            await c._log(
                f"[LEARNING_FINISHED] zone={zone_name} delta={round(delta)} "
                f"prev={prev} new={new_value} samples={c.samples}"
            )

            await self._save()
        else:
            await c._log(
                f"[LEARNING_SKIP] zone={zone_name} delta={round(delta)} "
                f"expected_range=[{min_d},{max_d}]"
            )

        self._reset_learning_state()

    def _reset_learning_state(self):
        c = self.coordinator
        c.learning_active = False
        c.learning_zone = None
        c.learning_start_time = None
        c.ac_power_before = None

    async def _save(self):
        try:
            await self.store.async_save({
                "learned_power": self.coordinator.learned_power,
                "samples": self.coordinator.samples,
            })
        except Exception as e:
            await self.coordinator._log(f"[STORAGE_ERROR] {e}")

    async def reset_learning(self):
        """Reset all learned values."""
        c = self.coordinator

        for zone in c.config["zones"]:
            zone_name = zone.split(".")[-1]
            c.learned_power[zone_name] = 1200

        c.samples = 0

        await c._log("[LEARNING_RESET] all_zones")
        await self._save()
