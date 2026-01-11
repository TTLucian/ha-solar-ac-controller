from __future__ import annotations

from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util

from .const import DOMAIN


class SolarACDebugEntity(Entity):
    """Debug sensor exposing internal controller state."""

    _attr_should_poll = False

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_name = "Solar AC Controller Debug"
        self._attr_unique_id = "solar_ac_controller_debug"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "solar_ac_controller")},
            "name": "Solar AC Controller",
            "configuration_url": "https://github.com/TTLucian/ha-solar-ac-controller",
        }
        self._unsub: callable | None = None

    @property
    def state(self):
        """Expose a simple state value (number of active zones)."""
        c = self.coordinator
        zones = c.config.get("zones", [])
        active = [
            z
            for z in zones
            if (st := c.hass.states.get(z)) and st.state in ("heat", "cool", "on")
        ]
        return len(active)

    @property
    def extra_state_attributes(self):
        """Return full internal state for debugging."""
        c = self.coordinator

        # Learning confidence: 0.0 → 1.0
        confidence = min(1.0, c.samples / 10) if c.samples else 0.0

        # Per-zone mode snapshot
        zone_modes = {
            z: (c.hass.states.get(z).state if c.hass.states.get(z) else None)
            for z in c.config.get("zones", [])
        }

        return {
            "last_action": c.last_action,
            "learning_active": c.learning_active,
            "learning_zone": c.learning_zone,
            "learning_confidence": confidence,
            "samples": c.samples,
            "ema_30s": round(c.ema_30s, 2),
            "ema_5m": round(c.ema_5m, 2),
            "last_add_conf": getattr(c, "last_add_conf", None),
            "last_remove_conf": getattr(c, "last_remove_conf", None),
            "last_action_start_ts": getattr(c, "last_action_start_ts", None),
            "last_action_duration": getattr(c, "last_action_duration", None),
            "learned_power": dict(c.learned_power) if c.learned_power is not None else {},
            "zone_last_changed": c.zone_last_changed,
            "zone_manual_lock_until": c.zone_manual_lock_until,
            "zone_modes": zone_modes,
            "action_delay_seconds": getattr(c, "action_delay_seconds", None),
            "master_off_since": int(c.master_off_since) if c.master_off_since else None,
            "master_off_since_iso": dt_util.utc_from_timestamp(c.master_off_since).isoformat()
            if c.master_off_since
            else None,
            "last_panic_ts": int(c.last_panic_ts) if c.last_panic_ts else None,
        }

    async def async_added_to_hass(self):
        """Register for coordinator updates and keep unsubscribe handle."""
        self._unsub = self.coordinator.async_add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        """Cleanup listener."""
        if callable(self._unsub):
            try:
                self._unsub()
            except Exception:
                # Best-effort cleanup; do not raise during unload
                pass
