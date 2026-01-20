# custom_components/solar_ac_controller/coordinator.py
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AC_POWER_SENSOR,
    CONF_AC_SWITCH,
    CONF_GRID_SENSOR,
    CONF_MANUAL_LOCK_SECONDS,
    CONF_PANIC_DELAY,
    CONF_PANIC_THRESHOLD,
    CONF_SHORT_CYCLE_OFF_SECONDS,
    CONF_SHORT_CYCLE_ON_SECONDS,
    CONF_SOLAR_SENSOR,
    CONF_SOLAR_THRESHOLD_OFF,
    CONF_SOLAR_THRESHOLD_ON,
    CONF_ZONES,
    CONF_ADD_CONFIDENCE,
    CONF_REMOVE_CONFIDENCE,
    CONF_INITIAL_LEARNED_POWER,
    CONF_ACTION_DELAY_SECONDS,
    CONF_SEASON_MODE,
    CONF_ENABLE_TEMP_MODULATION,
    CONF_MAX_TEMP_WINTER,
    CONF_MIN_TEMP_SUMMER,
    CONF_ZONE_TEMP_SENSORS,
    CONF_ZONE_MANUAL_POWER,
    DEFAULT_INITIAL_LEARNED_POWER,
    DEFAULT_SOLAR_THRESHOLD_ON,
    DEFAULT_SOLAR_THRESHOLD_OFF,
    DEFAULT_PANIC_THRESHOLD,
    DEFAULT_PANIC_DELAY,
    DEFAULT_MANUAL_LOCK_SECONDS,
    DEFAULT_SHORT_CYCLE_ON_SECONDS,
    DEFAULT_SHORT_CYCLE_OFF_SECONDS,
    DEFAULT_ACTION_DELAY_SECONDS,
    DEFAULT_ADD_CONFIDENCE,
    DEFAULT_REMOVE_CONFIDENCE,
    DEFAULT_SEASON_MODE,
    DEFAULT_ENABLE_TEMP_MODULATION,
    DEFAULT_MAX_TEMP_WINTER,
    DEFAULT_MIN_TEMP_SUMMER,
)
from .panic import PanicManager
from .zones import ZoneManager
from .decisions import DecisionEngine
from .actions import ActionExecutor

if TYPE_CHECKING:
    from .controller import SolarACController

_LOGGER = logging.getLogger(__name__)

_EMA_RESET_AFTER_OFF_SECONDS = 600




class SolarACCoordinator(DataUpdateCoordinator):
    # Note: Breadcrumb for diagnostics (reason for last no-op or decision)
    note: str = ""

    async def async_set_integration_enabled(self, enabled: bool) -> None:
        """Toggle the integration logic and trigger a refresh."""
        self.integration_enabled = enabled
        await self._log(f"Integration {'enabled' if enabled else 'disabled'} by user.")
        self.async_update_listeners()


    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: Any,
        store: Any,
        stored: dict[str, Any] | None,
        version: str | None = None,
    ) -> None:

        super().__init__(
            hass,
            logger=_LOGGER,
            update_interval=timedelta(seconds=5),
        )
        self.hass = hass
        self.config_entry = config_entry
        self.config = {**dict(config_entry.data), **dict(config_entry.options)}
        self.store = store
        self.version = version
        self.zone_manual_power = {}
        self.integration_enabled = True
        self.zone_manager = ZoneManager(self)
        self.panic_manager = PanicManager(self)
        self.decision_engine = DecisionEngine(self)
        self.action_executor = ActionExecutor(self)
        self.next_zone = None
        self.last_zone = None
        self.zone_last_changed = {}
        self.zone_last_changed_type = {}
        self.zone_last_state = {}
        self.zone_manual_lock_until = {}
        self.zone_temp_sensors = {}
        self.master_last_state = None
        self.master_last_action_time = None
        self.master_manual_lock_state = None
        self.required_export = None
        self.export_margin = None
        self.master_off_since = None

        # Enable temperature modulation
        self.enable_temp_modulation = bool(
            self.config_entry.options.get(
                CONF_ENABLE_TEMP_MODULATION,
                self.config_entry.data.get(
                    CONF_ENABLE_TEMP_MODULATION, DEFAULT_ENABLE_TEMP_MODULATION
                ),
            )
        )

        # Comfort temperature targets (C)
        self.max_temp_winter = float(
            self.config_entry.options.get(
                CONF_MAX_TEMP_WINTER,
                self.config_entry.data.get(
                    CONF_MAX_TEMP_WINTER, DEFAULT_MAX_TEMP_WINTER
                ),
            )
        )
        self.min_temp_summer = float(
            self.config_entry.options.get(
                CONF_MIN_TEMP_SUMMER,
                self.config_entry.data.get(
                    CONF_MIN_TEMP_SUMMER, DEFAULT_MIN_TEMP_SUMMER
                ),
            )
        )


        # Build zone→sensor mapping from parallel lists into a dict
        zones_list = self.config.get(CONF_ZONES, [])
        zone_temp_sensors_list = list(
            self.config_entry.options.get(
                CONF_ZONE_TEMP_SENSORS,
                self.config_entry.data.get(CONF_ZONE_TEMP_SENSORS, []),
            )
            or []
        )
        self.zone_temp_sensors = {}
        for idx, zone_id in enumerate(zones_list):
            if idx < len(zone_temp_sensors_list) and zone_temp_sensors_list[idx]:
                self.zone_temp_sensors[zone_id] = zone_temp_sensors_list[idx]

        # Build zone→manual power mapping from parallel input (list or comma-separated text)
        raw_manual = self.config_entry.options.get(
            CONF_ZONE_MANUAL_POWER,
            self.config_entry.data.get(CONF_ZONE_MANUAL_POWER, []),
        )
        if isinstance(raw_manual, str):
            parts = [p.strip() for p in raw_manual.split(",")]
            self.zone_manual_power = {
                zone: float(val)
                for zone, val in (p.split(":") for p in parts if ":" in p)
                if zone and val
            }
        elif isinstance(raw_manual, (list, tuple)):
            for item in list(raw_manual):
                if isinstance(item, str) and ":" in item:
                    zone, val = item.split(":", 1)
                    try:
                        self.zone_manual_power[zone.strip()] = float(val)
                    except Exception:
                        continue

        # Initialize other attributes needed later
        self.panic_threshold = float(self.config.get(CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD))
        self.panic_delay = int(self.config.get(CONF_PANIC_DELAY, DEFAULT_PANIC_DELAY))
        self.manual_lock_seconds = int(self.config.get(CONF_MANUAL_LOCK_SECONDS, DEFAULT_MANUAL_LOCK_SECONDS))
        self.short_cycle_on_seconds = int(self.config.get(CONF_SHORT_CYCLE_ON_SECONDS, DEFAULT_SHORT_CYCLE_ON_SECONDS))
        self.short_cycle_off_seconds = int(self.config.get(CONF_SHORT_CYCLE_OFF_SECONDS, DEFAULT_SHORT_CYCLE_OFF_SECONDS))
        self.action_delay_seconds = int(self.config.get(CONF_ACTION_DELAY_SECONDS, DEFAULT_ACTION_DELAY_SECONDS))
        self.add_confidence_threshold = float(self.config.get(CONF_ADD_CONFIDENCE, DEFAULT_ADD_CONFIDENCE))
        self.remove_confidence_threshold = float(self.config.get(CONF_REMOVE_CONFIDENCE, DEFAULT_REMOVE_CONFIDENCE))

        # Controller and confidence tracking
        from .controller import SolarACController
        self.controller = SolarACController(self.hass, self, self.store)
        self.last_add_conf = 0.0
        self.last_remove_conf = 0.0
        self.confidence = 0.0
        self.last_action_start_ts = None
        self.last_action_duration = None
        self._panic_task = None
        self.last_panic_ts = None


        # Initial learned power
        self.initial_learned_power = config_entry.options.get(
            CONF_INITIAL_LEARNED_POWER,
            config_entry.data.get(
                CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER
            ),
        )

        # Stored migration
        stored = stored or {}
        raw_learned = stored.get("learned_power", {}) or {}
        # Band learning removed: ignore learned_power_bands
        raw_samples = stored.get("samples", 0) or 0

        self.learned_power = {}
        self.samples = int(raw_samples)


        if isinstance(raw_learned, dict):
            for zone_name, val in raw_learned.items():
                if isinstance(val, (int, float)):
                    v = float(val)
                    self.learned_power[zone_name] = {"default": v, "heat": v, "cool": v}
                elif isinstance(val, dict):
                    normalized = {}
                    for k, vv in val.items():
                        try:
                            normalized[k.lower()] = float(vv)
                        except Exception:
                            continue
                    if "default" not in normalized:
                        normalized["default"] = normalized.get(
                            "heat",
                            normalized.get("cool", float(self.initial_learned_power)),
                        )
                    if "heat" not in normalized:
                        normalized["heat"] = normalized["default"]
                    if "cool" not in normalized:
                        normalized["cool"] = normalized["default"]
                    self.learned_power[zone_name] = normalized
                else:
                    self.learned_power[zone_name] = {
                        "default": float(self.initial_learned_power),
                        "heat": float(self.initial_learned_power),
                        "cool": float(self.initial_learned_power),
                    }
        else:
            self.learned_power = {}








        # Internal state
        self.last_action = None
        self.learning_active = False
        self.learning_start_time = None
        self.ac_power_before = None
        self.learning_zone = None
        self.ema_30s = 0.0
        self.ema_5m = 0.0

        # Season mode (manual selection: heat or cool)
    @property
    def season_mode(self) -> str:
        return (
            self.config_entry.options.get(
                CONF_SEASON_MODE,
                self.config_entry.data.get(CONF_SEASON_MODE, DEFAULT_SEASON_MODE),
            )
            or DEFAULT_SEASON_MODE
        )

    @season_mode.setter
    def season_mode(self, value: str):
        # This setter is only for runtime; persistent update is handled by the select entity
        self.config_entry.options = {**self.config_entry.options, CONF_SEASON_MODE: value}

    # -------------------------------------------------------------------------
    # Helper accessors for learned_power (abstracts storage format)
    # -------------------------------------------------------------------------
    def get_learned_power(
        self, zone_name: str, mode: str | None = None, band: str | None = None
    ) -> float:
        """Return learned power for a zone and mode/band, or default if missing."""
        entry = self.learned_power.get(zone_name)
        if entry is None:
            return float(self.initial_learned_power)
        if isinstance(entry, dict):
            val = None
            if mode and mode in entry:
                val = entry.get(mode)
            elif "default" in entry:
                val = entry.get("default")
            elif "heat" in entry:
                val = entry.get("heat")
            elif "cool" in entry:
                val = entry.get("cool")
            if val is not None:
                return float(val)
            return float(self.initial_learned_power)
        try:
            return float(entry)
        except Exception:
            return float(self.initial_learned_power)

    def set_learned_power(
        self,
        zone_name: str,
        value: float,
        mode: str | None = None,
    ) -> None:
        """Set learned power for a zone and mode with simple outlier filtering and smoothing.

        Goals:
        - Ignore clearly inconsistent samples (too high/low vs reasonable bounds or prior value)
        - Smooth accepted samples into the learned value (EMA-style)
        - Keep schema stable; no per-sample storage required
        """
        try:
            new_sample = float(value)
        except (TypeError, ValueError):
            return

        # Reasonable absolute bounds for a single zone incremental draw (W)
        MIN_W = 200.0
        MAX_W = 3000.0
        # Relative tolerance around existing learned value (± fraction)
        REL_TOL = 0.5  # accept within ±50% of current learned value
        # Smoothing factor for EMA update
        ALPHA = 0.3

        # Initialize zone entry if missing

        if zone_name not in self.learned_power or not isinstance(
            self.learned_power.get(zone_name), dict
        ):
            val = self.learned_power.get(zone_name)
            if isinstance(val, (int, float)):
                base = float(val)
            else:
                base = float(self.initial_learned_power)
            self.learned_power[zone_name] = {
                "default": base,
                "heat": base,
                "cool": base,
            }


        entry = self.learned_power[zone_name]
        val = entry.get(mode or "default", entry.get("default", self.initial_learned_power))
        if val is not None:
            current = float(val)
        else:
            current = float(self.initial_learned_power)

        # Absolute outlier filter
        if not (MIN_W <= new_sample <= MAX_W):
            try:
                _LOGGER.debug(
                    "Discarding outlier sample for %s: %sW outside [%s,%s]",
                    zone_name,
                    new_sample,
                    MIN_W,
                    MAX_W,
                )
            except Exception:
                pass
            return

        # Relative outlier filter (only apply if we have a meaningful current value)
        lower = max(MIN_W, current * (1.0 - REL_TOL))
        upper = min(MAX_W, current * (1.0 + REL_TOL))
        if not (lower <= new_sample <= upper):
            try:
                _LOGGER.debug(
                    "Discarding relative outlier for %s: %sW outside [%s,%s] around current %sW",
                    zone_name,
                    new_sample,
                    round(lower, 1),
                    round(upper, 1),
                    round(current, 1),
                )
            except Exception:
                pass
            return

        # Smooth update
        updated = (ALPHA * new_sample) + ((1.0 - ALPHA) * current)
        updated = round(updated)  # store whole watts only

        # Update mode-specific and default values
        if mode:
            entry[mode] = float(updated)
        entry["default"] = float(updated)
        if "heat" not in entry:
            entry["heat"] = entry["default"]
        if "cool" not in entry:
            entry["cool"] = entry["default"]



    async def _persist_learned_values(self) -> None:
        """Persist learned values to storage."""
        try:
            payload = {
                "learned_power": self._rounded_power(self.learned_power),
                "samples": int(self.samples),
            }
            await self.store.async_save(payload)
        except Exception as exc:
            _LOGGER.exception("Error saving learned values: %s", exc)
            try:
                await self._log(f"[STORAGE_ERROR] {exc}")
            except Exception:
                _LOGGER.exception("Failed to write storage error to coordinator log")

    def _rounded_power(self, value: Any) -> Any:
        """Recursively round power values to whole watts for clean storage."""
        if isinstance(value, dict):
            return {k: self._rounded_power(v) for k, v in value.items()}
        try:
            return int(round(float(value)))
        except Exception:
            return value

    # -------------------------------------------------------------------------
    # Minimal async logging hook used by coordinator and controller
    # -------------------------------------------------------------------------
    async def _log(self, message: str) -> None:
        """Async logging hook used by coordinator and controller."""
        """Async logging hook used by coordinator and controller."""
        try:
            # Keep this simple and non-blocking; expand if persistent logs are desired
            _LOGGER.info(message)
        except Exception:
            _LOGGER.debug("Failed to write coordinator log message: %s", message)

    # -------------------------------------------------------------------------
    # Main update loop
    # -------------------------------------------------------------------------


    async def _async_update_data(self) -> None:
        """Main loop executed every 5 seconds."""
        # Integration enable/disable logic
        if hasattr(self, "integration_enabled") and not self.integration_enabled:
            self.last_action = "integration_disabled"
            self.note = "Integration disabled by user."
            _LOGGER.debug("Integration disabled, skipping all logic.")
            return

        # 1. Read sensors (grid, solar, ac_power)
        grid_state = self.hass.states.get(self.config.get(CONF_GRID_SENSOR))
        solar_state = self.hass.states.get(self.config.get(CONF_SOLAR_SENSOR))
        ac_state = self.hass.states.get(self.config.get(CONF_AC_POWER_SENSOR))

        if not grid_state or not solar_state or not ac_state:
            self.note = "Missing sensor state, skipping cycle."
            _LOGGER.debug("Missing sensor state, skipping cycle")
            return

        if ac_state.state in ("unknown", "unavailable"):
            self.last_action = "ac_sensor_unavailable"
            self.note = "AC power sensor unavailable, skipping cycle."
            _LOGGER.debug("AC power sensor unavailable, skipping cycle")
            return

        try:
            grid_raw = float(grid_state.state)
            solar = float(solar_state.state)
            ac_power = float(ac_state.state)
        except (ValueError, TypeError):
            self.note = "Non-numeric sensor value, skipping cycle."
            _LOGGER.debug("Non-numeric sensor value, skipping cycle")
            return

        _LOGGER.debug(
            "Cycle sensors: grid_raw=%s solar=%s ac_power=%s", grid_raw, solar, ac_power
        )

        # EMA updates
        self._update_ema(grid_raw)

        # 2. Master switch auto-control (based ONLY on solar production)
        await self._handle_master_switch(solar)

        # 3. Freeze zone management when solar is too low (regardless of master switch state)
        # This must happen BEFORE any temperature/season reading to ensure complete freeze
        try:
            off_threshold = float(
                self.config.get(CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF)
            )
        except (TypeError, ValueError):
            off_threshold = DEFAULT_SOLAR_THRESHOLD_OFF

        if solar <= off_threshold:
            # Ensure any running tasks are cancelled and learning reset
            await self._perform_freeze_cleanup()
            self.last_action = "solar_too_low"
            self.note = f"Solar {round(solar)}W <= threshold_off {off_threshold}W: freezing zone management."
            await self._log(
                f"[FREEZE] solar={round(solar)} <= threshold_off={off_threshold}, freezing zone management"
            )
            return

        # 4. Update zone temperatures for comfort target checking
        self._read_zone_temps()

        # 5. EMA updates

        # 6. Determine zones and detect manual overrides
        if not hasattr(self, "zone_manager") or self.zone_manager is None:
            _LOGGER.error("zone_manager is not initialized! Skipping update cycle.")
            self.last_action = "zone_manager_uninitialized"
            return

        active_zones = await self.zone_manager.update_zone_states_and_overrides()
        on_count = len(active_zones)

        # 7. Compute required export and confidences
        next_zone, last_zone = self.zone_manager.select_next_and_last_zone(active_zones)
        required_export = self._compute_required_export(
            next_zone, mode=self.season_mode
        )
        export = -self.ema_30s
        import_power = self.ema_5m

        # Store for sensors
        self.next_zone = next_zone
        self.last_zone = last_zone
        self.required_export = required_export
        # Track source for diagnostics: manual override vs learned power
        try:
            if (
                next_zone
                and isinstance(self.zone_manual_power, dict)
                and next_zone in self.zone_manual_power
            ):
                self.required_export_source = "Manual Power Override"
            elif self.last_action == "panic_cooldown":
                self.required_export_source = "Panic Recovery"
            elif self.last_action == "integration_disabled":
                self.required_export_source = "Integration Disabled"
            elif self.last_action == "solar_too_low":
                self.required_export_source = "Solar Freeze"
            else:
                self.required_export_source = "Learned Power"
        except Exception:
            self.required_export_source = "Learned Power"
        self.export_margin = (
            None if required_export is None else export - required_export
        )

        self.last_add_conf = self.decision_engine.compute_add_conf(
            export=export,
            required_export=required_export,
            last_zone=last_zone,
        )
        self.last_remove_conf = self.decision_engine.compute_remove_conf(
            import_power=import_power,
            last_zone=last_zone,
        )

        # Unified confidence
        self.confidence = self.last_add_conf - self.last_remove_conf

        now_ts = dt_util.utcnow().timestamp()

        # 8. Learning timeout
        if self.learning_active and self.learning_start_time:
            if now_ts - self.learning_start_time >= 360:
                await self._log(f"[LEARNING_TIMEOUT] zone={self.learning_zone}")
                await self.controller.finish_learning()
                return

        # 9. Panic logic
        if self.panic_manager.should_panic(on_count):
            self.note = "Panic triggered: grid import exceeded threshold with multiple zones active."
            await self.panic_manager.schedule_panic(active_zones)
            return

        # 10. Panic cooldown
        if self.panic_manager.is_in_cooldown(now_ts):
            self.last_action = "panic_cooldown"
            self.note = "Panic cooldown active: skipping add/remove decisions."
            await self._log("[PANIC_COOLDOWN] skipping add/remove decisions")
            return

        # 11. ADD zone decision
        if next_zone and self.decision_engine.should_add_zone(
            next_zone, required_export if required_export is not None else 0.0
        ):
            self.note = f"Adding zone {next_zone}: conditions met."
            await self.action_executor.attempt_add_zone(
                next_zone, ac_power, export, required_export if required_export is not None else 0.0
            )
            return

        # 11. REMOVE zone decision
        if last_zone and self.decision_engine.should_remove_zone(
            last_zone, import_power, active_zones
        ):
            self.note = f"Removing zone {last_zone}: conditions met."
            await self.action_executor.attempt_remove_zone(last_zone, import_power)
            return

        # 12. SYSTEM BALANCED
        self.last_action = "balanced"
        self.note = f"No action: system balanced. ema30={round(self.ema_30s)}, ema5m={round(self.ema_5m)}, zones={on_count}, samples={self.samples}"
        await self._log(
            f"[SYSTEM_BALANCED] ema30={round(self.ema_30s)} "
            f"ema5m={round(self.ema_5m)} zones={on_count} samples={self.samples}"
        )

    # -------------------------------------------------------------------------
    # EMA / metrics / guards
    # -------------------------------------------------------------------------
    def _update_ema(self, grid_raw: float) -> None:
        """Update EMA metrics for grid power."""
        self.ema_30s = 0.25 * grid_raw + 0.75 * self.ema_30s
        self.ema_5m = 0.03 * grid_raw + 0.97 * self.ema_5m

    def _compute_required_export(
        self, next_zone: str | None, mode: str | None = None
    ) -> float | None:
        """Compute required export for the next zone.

        Priority:
        1. Manual power override (if configured for zone)
        2. Mode-aware learned power (if available)
        """
        if not next_zone:
            return None

        # Check for manual power override first
        if next_zone in self.zone_manual_power:
            return self.zone_manual_power[next_zone]

        zone_name = next_zone.split(".")[-1]
        lp = self.get_learned_power(zone_name, mode=mode or "default")
        return float(lp)

    def _read_zone_temps(self) -> None:
        """
        Read current temperatures for all configured zones.

        Priority:
        1. External temperature sensor (if configured)
        2. Climate entity's current_temperature attribute (if zone is climate)
        3. None (temperature unavailable)
        """
        self.zone_current_temps = {}

        for zone_id, temp_sensor_id in self.zone_temp_sensors.items():
            # Try external sensor first
            if temp_sensor_id:
                st = self.hass.states.get(temp_sensor_id)
                if st and st.state not in ("unknown", "unavailable", ""):
                    try:
                        self.zone_current_temps[zone_id] = float(st.state)
                        continue
                    except (TypeError, ValueError):
                        pass

            # Fallback: try climate entity current_temperature attribute
            zone_state = self.hass.states.get(zone_id)
            if zone_state and zone_state.domain == "climate":
                current_temp = zone_state.attributes.get("current_temperature")
                if current_temp is not None:
                    try:
                        self.zone_current_temps[zone_id] = float(current_temp)
                        continue
                    except (TypeError, ValueError):
                        pass

            # Temperature unavailable
            self.zone_current_temps[zone_id] = None

    def _all_active_zones_at_target(self, zone_to_check: str | None) -> bool:
        """
        Check if the specified zone has reached its comfort target.

        Returns True if the zone is at or above/below target:
        - In heat mode: zone >= max_temp_winter
        - In cool mode: zone <= min_temp_summer

        Returns False if zone has no sensor or is not at target.
        """
        if not zone_to_check or not self.season_mode:
            return True  # No zone specified or no season, don't block

        current_temp = self.zone_current_temps.get(zone_to_check)

        # Missing sensor: assume "not at target" (conservative, keeps zone on)
        if current_temp is None:
            return False

        if self.season_mode == "heat":
            # Heat: zone must be at or above winter target
            return current_temp >= self.max_temp_winter
        elif self.season_mode == "cool":
            # Cool: zone must be at or below summer target
            return current_temp <= self.min_temp_summer

        return True  # Shouldn't reach here, but don't block by default

    async def _perform_freeze_cleanup(self) -> None:
        """Cancel tasks and reset learning state when master is off."""
        """Cancel tasks and reset learning state when master is off."""
        # Cancel panic task
        if self._panic_task and not self._panic_task.done():
            try:
                self._panic_task.cancel()
            except Exception:
                _LOGGER.debug("Failed to cancel panic task")
            self._panic_task = None

        # Reset controller learning state (safe)
        try:
            if getattr(self, "controller", None) is not None:
                await self.controller._reset_learning_state_async()
        except Exception:
            _LOGGER.debug(
                "Controller reset learning method failed or controller not set"
            )

        # Track master_off_since for EMA reset
        now_ts = dt_util.utcnow().timestamp()
        if self.master_off_since is None:
            self.master_off_since = now_ts

        # Reset EMA after long OFF
        if now_ts - self.master_off_since >= _EMA_RESET_AFTER_OFF_SECONDS:
            if self.ema_30s != 0.0 or self.ema_5m != 0.0:
                await self._log("[EMA_RESET_AFTER_MASTER_OFF] resetting EMA")
            self.ema_30s = 0.0
            self.ema_5m = 0.0

    async def _call_entity_service(self, entity_id: str, turn_on: bool) -> None:
        """Call turn_on/turn_off service for the entity's domain, with climate fallback."""
        domain = entity_id.split(".")[0]
        service = "turn_on" if turn_on else "turn_off"

        # For climate entities being turned on: set HVAC mode first based on season
        if turn_on and domain == "climate" and self.season_mode in ("heat", "cool"):
            try:
                await self.hass.services.async_call(
                    "climate",
                    "set_hvac_mode",
                    {"entity_id": entity_id, "hvac_mode": self.season_mode},
                    blocking=True,
                )
                _LOGGER.debug(
                    "Set HVAC mode to '%s' for %s before turning on",
                    self.season_mode,
                    entity_id,
                )
            except Exception as e:
                _LOGGER.warning(
                    "Failed to set HVAC mode '%s' for %s: %s — will proceed with turn_on",
                    self.season_mode,
                    entity_id,
                    e,
                )

        try:
            await self.hass.services.async_call(
                domain,
                service,
                {"entity_id": entity_id},
                blocking=True,
            )
            return
        except Exception as e:
            _LOGGER.debug(
                "Primary service %s.%s failed for %s: %s",
                domain,
                service,
                entity_id,
                e,
            )

        try:
            await self.hass.services.async_call(
                "climate",
                service,
                {"entity_id": entity_id},
                blocking=True,
            )
            _LOGGER.warning(
                "Primary service %s.%s failed for %s — used climate.%s as fallback",
                domain,
                service,
                entity_id,
                service,
            )
            return
        except Exception as e:
            _LOGGER.exception(
                "Fallback climate.%s failed for %s: %s", service, entity_id, e
            )

    # -------------------------------------------------------------------------
    # Master switch control
    # -------------------------------------------------------------------------
    async def _handle_master_switch(self, solar: float) -> None:
        """Master relay control with sticky manual lock until natural solar cycle aligns."""
        ac_switch = self.config.get(CONF_AC_SWITCH)
        if not ac_switch:
            return

        try:
            on_threshold = float(
                self.config.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON)
            )
        except (TypeError, ValueError):
            on_threshold = DEFAULT_SOLAR_THRESHOLD_ON

        try:
            off_threshold = float(
                self.config.get(CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF)
            )
        except (TypeError, ValueError):
            off_threshold = DEFAULT_SOLAR_THRESHOLD_OFF

        switch_state_obj = self.hass.states.get(ac_switch)
        if not switch_state_obj:
            return

        switch_state = switch_state_obj.state

        # Detect manual changes (state changed without recent coordinator action)
        if (
            self.master_last_state is not None
            and switch_state != self.master_last_state
        ):
            now = dt_util.utcnow().timestamp()
            # If no recent coordinator action (within 10s), it's a manual change
            if (
                self.master_last_action_time is None
                or (now - self.master_last_action_time) > 10
            ):
                self.master_manual_lock_state = switch_state
                await self._log(
                    f"[MASTER_MANUAL_LOCK] detected manual change to {switch_state}, locking until natural cycle aligns"
                )

        # Check if lock should be released
        if self.master_manual_lock_state is not None:
            # Release lock if locked ON and solar would naturally turn it ON
            if self.master_manual_lock_state == "on" and solar >= on_threshold:
                await self._log(
                    f"[MASTER_LOCK_RELEASE] solar={round(solar)} >= threshold_on={on_threshold}, resuming auto-control"
                )
                self.master_manual_lock_state = None
            # Release lock if locked OFF and solar would naturally turn it OFF
            elif self.master_manual_lock_state == "off" and solar <= off_threshold:
                await self._log(
                    f"[MASTER_LOCK_RELEASE] solar={round(solar)} <= threshold_off={off_threshold}, resuming auto-control"
                )
                self.master_manual_lock_state = None
            else:
                # Still locked, skip auto-control
                self.master_last_state = switch_state
                return

        # Update last known state
        self.master_last_state = switch_state

        # Normal auto-control (only when not locked)
        # Turn ON when solar is above or equal to ON threshold
        if solar >= on_threshold and switch_state == "off":
            await self._log(
                f"[MASTER_ON] solar={round(solar)} threshold_on={on_threshold}"
            )
            await self.hass.services.async_call(
                "switch",
                "turn_on",
                {"entity_id": ac_switch},
                blocking=True,
            )
            self.last_action = "master_on"
            self.master_last_action_time = dt_util.utcnow().timestamp()
            # reset master_off_since when turned on
            self.master_off_since = None
            return

        # Turn OFF when solar is below or equal to OFF threshold
        if solar <= off_threshold and switch_state == "on":
            await self._log(
                f"[MASTER_OFF_TRIGGER] solar={round(solar)} threshold_off={off_threshold}"
            )
            await self.hass.services.async_call(
                "switch",
                "turn_off",
                {"entity_id": ac_switch},
                blocking=True,
            )
            self.last_action = "master_off"
            self.master_last_action_time = dt_util.utcnow().timestamp()
            # mark master_off_since for EMA reset logic
            self.master_off_since = dt_util.utcnow().timestamp()
            return
