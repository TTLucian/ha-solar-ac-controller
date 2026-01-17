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
    CONF_OUTSIDE_SENSOR,
    CONF_ENABLE_AUTO_SEASON,
    CONF_ENABLE_TEMP_MODULATION,
    CONF_MASTER_OFF_IN_NEUTRAL,
    CONF_HEAT_ON_BELOW,
    CONF_HEAT_OFF_ABOVE,
    CONF_COOL_ON_ABOVE,
    CONF_COOL_OFF_BELOW,
    CONF_BAND_COLD_MAX,
    CONF_BAND_MILD_COLD_MAX,
    CONF_BAND_MILD_HOT_MAX,
    CONF_MAX_TEMP_WINTER,
    CONF_MIN_TEMP_SUMMER,
    CONF_ZONE_TEMP_SENSORS,
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
    DEFAULT_ENABLE_AUTO_SEASON,
    DEFAULT_ENABLE_TEMP_MODULATION,
    DEFAULT_MASTER_OFF_IN_NEUTRAL,
    DEFAULT_HEAT_ON_BELOW,
    DEFAULT_HEAT_OFF_ABOVE,
    DEFAULT_COOL_ON_ABOVE,
    DEFAULT_COOL_OFF_BELOW,
    DEFAULT_BAND_COLD_MAX,
    DEFAULT_BAND_MILD_COLD_MAX,
    DEFAULT_BAND_MILD_HOT_MAX,
    DEFAULT_MAX_TEMP_WINTER,
    DEFAULT_MIN_TEMP_SUMMER,
)
from .season import SeasonManager
from .panic import PanicManager
from .zones import ZoneManager
from .decisions import DecisionEngine
from .actions import ActionExecutor

if TYPE_CHECKING:
    from .controller import SolarACController

_LOGGER = logging.getLogger(__name__)

_EMA_RESET_AFTER_OFF_SECONDS = 600



class SolarACCoordinator(DataUpdateCoordinator):
    """
    Main control loop for the Solar AC Controller.

    Handles all state, learning, zone logic, and device actions.
    Storage migrations are handled in __init__.py; see STORAGE_VERSION and migration docstring there.
    """

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
            name="Solar AC Controller",
            update_interval=timedelta(seconds=5),
        )

        self.hass = hass
        self.config_entry = config_entry
        self.config = {**dict(config_entry.data), **dict(config_entry.options)}
        self.store = store
        self.version = version

        # Initial learned power
        self.initial_learned_power = config_entry.options.get(
            CONF_INITIAL_LEARNED_POWER,
            config_entry.data.get(CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER),
        )

        # Stored migration
        stored = stored or {}
        raw_learned = stored.get("learned_power", {}) or {}
        raw_learned_bands = stored.get("learned_power_bands", {}) or {}
        raw_samples = stored.get("samples", 0) or 0

        self.learned_power: dict[str, dict[str, float]] = {}
        self.learned_power_bands: dict[str, dict[str, dict[str, float]]] = {}
        self.samples: int = int(raw_samples)

        migrated = False
        if isinstance(raw_learned, dict):
            for zone_name, val in raw_learned.items():
                if isinstance(val, (int, float)):
                    migrated = True
                    v = float(val)
                    self.learned_power[zone_name] = {"default": v, "heat": v, "cool": v}
                elif isinstance(val, dict):
                    normalized: dict[str, float] = {}
                    for k, vv in val.items():
                        try:
                            normalized[k.lower()] = float(vv)
                        except Exception:
                            continue
                    if "default" not in normalized:
                        normalized["default"] = normalized.get(
                            "heat", normalized.get("cool", float(self.initial_learned_power))
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

        if isinstance(raw_learned_bands, dict):
            for zone_name, mode_map in raw_learned_bands.items():
                if not isinstance(mode_map, dict):
                    continue
                self.learned_power_bands[zone_name] = {}
                for mode, band_map in mode_map.items():
                    if not isinstance(band_map, dict):
                        continue
                    try:
                        normalized_band = {str(k).lower(): float(v) for k, v in band_map.items()}
                        self.learned_power_bands[zone_name][mode.lower()] = normalized_band
                    except Exception:
                        continue

        if migrated:
            try:
                payload = {
                    "learned_power": dict(self.learned_power),
                    "learned_power_bands": dict(raw_learned_bands) if isinstance(raw_learned_bands, dict) else {},
                    "samples": int(self.samples),
                }

                async def _save_payload():
                    try:
                        await self.store.async_save(payload)
                        _LOGGER.info("Migrated legacy learned_power to per-mode structure and saved storage")
                    except Exception as exc:
                        _LOGGER.exception("Failed to persist migrated learned_power: %s", exc)

                hass.async_create_task(_save_payload())
            except Exception as exc:
                _LOGGER.exception("Failed to schedule persist of migrated learned_power: %s", exc)

        # Internal state
        self.last_action: str | None = None
        self.learning_active: bool = False
        self.learning_start_time: float | None = None
        self.ac_power_before: float | None = None
        self.learning_zone: str | None = None
        self.learning_band: str | None = None

        self.ema_30s: float = 0.0
        self.ema_5m: float = 0.0

        # Outdoor/season context
        self.outside_temp: float | None = None
        self.outside_temp_rolling_mean: float | None = None
        self.outside_band: str | None = None
        self.season_mode: str | None = None
        self.enable_auto_season: bool = bool(
            self.config_entry.options.get(CONF_ENABLE_AUTO_SEASON, self.config_entry.data.get(CONF_ENABLE_AUTO_SEASON, DEFAULT_ENABLE_AUTO_SEASON))
        )
        self.enable_temp_modulation: bool = bool(
            self.config_entry.options.get(CONF_ENABLE_TEMP_MODULATION, self.config_entry.data.get(CONF_ENABLE_TEMP_MODULATION, DEFAULT_ENABLE_TEMP_MODULATION))
        )
        self.master_off_in_neutral: bool = bool(
            self.config_entry.options.get(CONF_MASTER_OFF_IN_NEUTRAL, self.config_entry.data.get(CONF_MASTER_OFF_IN_NEUTRAL, DEFAULT_MASTER_OFF_IN_NEUTRAL))
        )
        self.heat_on_below: float = float(self.config_entry.options.get(CONF_HEAT_ON_BELOW, self.config_entry.data.get(CONF_HEAT_ON_BELOW, DEFAULT_HEAT_ON_BELOW)))
        self.heat_off_above: float = float(self.config_entry.options.get(CONF_HEAT_OFF_ABOVE, self.config_entry.data.get(CONF_HEAT_OFF_ABOVE, DEFAULT_HEAT_OFF_ABOVE)))
        self.cool_on_above: float = float(self.config_entry.options.get(CONF_COOL_ON_ABOVE, self.config_entry.data.get(CONF_COOL_ON_ABOVE, DEFAULT_COOL_ON_ABOVE)))
        self.cool_off_below: float = float(self.config_entry.options.get(CONF_COOL_OFF_BELOW, self.config_entry.data.get(CONF_COOL_OFF_BELOW, DEFAULT_COOL_OFF_BELOW)))
        self.band_cold_max: float = float(self.config_entry.options.get(CONF_BAND_COLD_MAX, self.config_entry.data.get(CONF_BAND_COLD_MAX, DEFAULT_BAND_COLD_MAX)))
        self.band_mild_cold_max: float = float(self.config_entry.options.get(CONF_BAND_MILD_COLD_MAX, self.config_entry.data.get(CONF_BAND_MILD_COLD_MAX, DEFAULT_BAND_MILD_COLD_MAX)))
        self.band_mild_hot_max: float = float(self.config_entry.options.get(CONF_BAND_MILD_HOT_MAX, self.config_entry.data.get(CONF_BAND_MILD_HOT_MAX, DEFAULT_BAND_MILD_HOT_MAX)))

        # Comfort temperature targets (C)
        self.max_temp_winter: float = float(self.config_entry.options.get(CONF_MAX_TEMP_WINTER, self.config_entry.data.get(CONF_MAX_TEMP_WINTER, DEFAULT_MAX_TEMP_WINTER)))
        self.min_temp_summer: float = float(self.config_entry.options.get(CONF_MIN_TEMP_SUMMER, self.config_entry.data.get(CONF_MIN_TEMP_SUMMER, DEFAULT_MIN_TEMP_SUMMER)))
        
        # Build zone→sensor mapping from parallel lists
        zones_list = self.config.get(CONF_ZONES, [])
        zone_temp_sensors_list: list[str] = list(self.config_entry.options.get(CONF_ZONE_TEMP_SENSORS, self.config_entry.data.get(CONF_ZONE_TEMP_SENSORS, [])) or [])
        self.zone_temp_sensors: dict[str, str] = {}
        for idx, zone_id in enumerate(zones_list):
            if idx < len(zone_temp_sensors_list) and zone_temp_sensors_list[idx]:
                self.zone_temp_sensors[zone_id] = zone_temp_sensors_list[idx]
        
        self.zone_current_temps: dict[str, float | None] = {}  # zone_id -> current temp or None

        # Disable temperature modulation if no zone temp sensors configured
        if not self.zone_temp_sensors:
            self.enable_temp_modulation = False
            _LOGGER.debug("Temperature modulation disabled: no zone temperature sensors configured")

        # Initialize SeasonManager for outdoor temp and season mode handling
        from .season import SeasonManager
        self.season_manager = SeasonManager(
            hass=hass,
            config=self.config,
            heat_on_below=self.heat_on_below,
            heat_off_above=self.heat_off_above,
            cool_on_above=self.cool_on_above,
            cool_off_below=self.cool_off_below,
            band_cold_max=self.band_cold_max,
            band_mild_cold_max=self.band_mild_cold_max,
            band_mild_hot_max=self.band_mild_hot_max,
            enable_auto_season=self.enable_auto_season,
        )

        self.zone_last_changed: dict[str, float] = {}
        self.zone_last_changed_type: dict[str, str] = {}
        self.zone_last_state: dict[str, str | None] = {}
        self.zone_manual_lock_until: dict[str, float] = {}

        # Use centralized defaults from const.py
        self.panic_threshold: float = float(self.config.get(CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD))
        self.panic_delay: int = int(self.config.get(CONF_PANIC_DELAY, DEFAULT_PANIC_DELAY))
        self.manual_lock_seconds: int = int(self.config.get(CONF_MANUAL_LOCK_SECONDS, DEFAULT_MANUAL_LOCK_SECONDS))
        self.short_cycle_on_seconds: int = int(self.config.get(CONF_SHORT_CYCLE_ON_SECONDS, DEFAULT_SHORT_CYCLE_ON_SECONDS))
        self.short_cycle_off_seconds: int = int(self.config.get(CONF_SHORT_CYCLE_OFF_SECONDS, DEFAULT_SHORT_CYCLE_OFF_SECONDS))
        self.action_delay_seconds: int = int(self.config.get(CONF_ACTION_DELAY_SECONDS, DEFAULT_ACTION_DELAY_SECONDS))

        self.add_confidence_threshold: float = float(
            self.config.get(CONF_ADD_CONFIDENCE, DEFAULT_ADD_CONFIDENCE)
        )
        self.remove_confidence_threshold: float = float(
            self.config.get(CONF_REMOVE_CONFIDENCE, DEFAULT_REMOVE_CONFIDENCE)
        )

        from .controller import SolarACController
        self.controller: "SolarACController" = SolarACController(hass, self, store)

        # Initialize sub-managers
        self.panic_manager = PanicManager(self)
        self.zone_manager = ZoneManager(self)
        self.decision_engine = DecisionEngine(self)
        self.action_executor = ActionExecutor(self)

        self.last_add_conf: float = 0.0
        self.last_remove_conf: float = 0.0
        self.confidence: float = 0.0
        self.last_action_start_ts: float | None = None
        self.last_action_duration: float | None = None

        self._panic_task: asyncio.Task | None = None

        self.last_panic_ts: float | None = None

        self.next_zone: str | None = None
        self.last_zone: str | None = None
        self.required_export: float | None = None
        self.export_margin: float | None = None

        # Track when master was turned off for EMA reset
        self.master_off_since: float | None = None

    # -------------------------------------------------------------------------
    # Helper accessors for learned_power (abstracts storage format)
    # -------------------------------------------------------------------------
    def get_learned_power(self, zone_name: str, mode: str | None = None, band: str | None = None) -> float:
        """Return learned power for a zone and mode/band, or default if missing."""
        if band:
            mode_map = self.learned_power_bands.get(zone_name, {}) if isinstance(self.learned_power_bands, dict) else {}
            band_map = mode_map.get(mode or "default") if isinstance(mode_map, dict) else None
            if isinstance(band_map, dict) and band in band_map:
                try:
                    return float(band_map[band])
                except Exception:
                    pass

        entry = self.learned_power.get(zone_name)
        if entry is None:
            return float(self.initial_learned_power)
        if isinstance(entry, dict):
            if mode and mode in entry:
                return float(entry.get(mode))
            if "default" in entry:
                return float(entry.get("default"))
            if "heat" in entry:
                return float(entry.get("heat"))
            if "cool" in entry:
                return float(entry.get("cool"))
            return float(self.initial_learned_power)
        try:
            return float(entry)
        except Exception:
            return float(self.initial_learned_power)

    def set_learned_power(self, zone_name: str, value: float, mode: str | None = None, band: str | None = None) -> None:
        """Set learned power for a zone and mode/band."""
        if band:
            if zone_name not in self.learned_power_bands or not isinstance(self.learned_power_bands.get(zone_name), dict):
                self.learned_power_bands[zone_name] = {}
            mode_map = self.learned_power_bands[zone_name].get(mode or "default")
            if not isinstance(mode_map, dict):
                mode_map = {}
            mode_map[band] = float(value)
            self.learned_power_bands[zone_name][mode or "default"] = mode_map

        if zone_name not in self.learned_power or not isinstance(self.learned_power.get(zone_name), dict):
            base = float(
                self.learned_power.get(zone_name)
                if isinstance(self.learned_power.get(zone_name), (int, float))
                else self.initial_learned_power
            )
            self.learned_power[zone_name] = {
                "default": base,
                "heat": base,
                "cool": base,
            }

        entry = self.learned_power[zone_name]
        if mode:
            entry[mode] = float(value)
        entry["default"] = float(value)
        if "heat" not in entry:
            entry["heat"] = entry["default"]
        if "cool" not in entry:
            entry["cool"] = entry["default"]

    async def _persist_learned_values(self) -> None:
        """Persist learned values to storage."""
        try:
            payload = {
                "learned_power": dict(self.learned_power),
                "learned_power_bands": dict(self.learned_power_bands),
                "samples": int(self.samples),
            }
            await self.store.async_save(payload)
        except Exception as exc:
            _LOGGER.exception("Error saving learned values: %s", exc)
            try:
                await self._log(f"[STORAGE_ERROR] {exc}")
            except Exception:
                _LOGGER.exception("Failed to write storage error to coordinator log")

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
        """Main loop executed every 5 seconds."""

        # 1. Read sensors (grid, solar, ac_power)
        grid_state = self.hass.states.get(self.config.get(CONF_GRID_SENSOR))
        solar_state = self.hass.states.get(self.config.get(CONF_SOLAR_SENSOR))
        ac_state = self.hass.states.get(self.config.get(CONF_AC_POWER_SENSOR))

        if not grid_state or not solar_state or not ac_state:
            _LOGGER.debug("Missing sensor state, skipping cycle")
            return

        if ac_state.state in ("unknown", "unavailable"):
            self.last_action = "ac_sensor_unavailable"
            _LOGGER.debug("AC power sensor unavailable, skipping cycle")
            return

        try:
            grid_raw = float(grid_state.state)
            solar = float(solar_state.state)
            ac_power = float(ac_state.state)
        except (ValueError, TypeError):
            _LOGGER.debug("Non-numeric sensor value, skipping cycle")
            return

        _LOGGER.debug(
            "Cycle sensors: grid_raw=%s solar=%s ac_power=%s", grid_raw, solar, ac_power
        )

        # 2. Master switch auto-control (based ONLY on solar production)
        await self._handle_master_switch(solar)

        # 3. If master exists and is OFF -> perform full freeze cleanup then return
        # This must happen BEFORE any temperature/season reading to ensure complete freeze
        ac_switch = self.config.get(CONF_AC_SWITCH)
        if ac_switch:
            switch_state_obj = self.hass.states.get(ac_switch)
            if switch_state_obj and switch_state_obj.state == "off":
                # Ensure any running tasks are cancelled and learning reset
                await self._perform_freeze_cleanup()
                self.last_action = "master_off"
                await self._log("[MASTER_OFF] master switch is off, freezing all calculations")
                return

        # 4. Outside temperature context (delegated to SeasonManager) - only if master is ON
        outside_temp = self.season_manager.read_outside_temp()
        self.outside_temp = outside_temp
        self.outside_temp_rolling_mean = self.season_manager.rolling_mean
        self.outside_band = self.season_manager.select_outside_band(outside_temp)
        self.season_mode = self.season_manager.update_season_mode(outside_temp)

        # Neutral mode freeze if configured
        if self.season_mode == "neutral" and self.master_off_in_neutral:
            ac_switch_cfg = self.config.get(CONF_AC_SWITCH)
            if ac_switch_cfg:
                try:
                    await self.hass.services.async_call(
                        "switch",
                        "turn_off",
                        {"entity_id": ac_switch_cfg},
                        blocking=True,
                    )
                except Exception:
                    _LOGGER.debug("Failed to turn off master switch while neutral")
            await self._perform_freeze_cleanup()
            self.last_action = "neutral"
            return

        # 5. EMA updates
        self._update_ema(grid_raw)

        # 5b. Read zone temperatures for comfort target checking
        self._read_zone_temps()

        # 6. Determine zones and detect manual overrides
        active_zones = await self.zone_manager.update_zone_states_and_overrides()
        on_count = len(active_zones)

        # 7. Compute required export and confidences
        next_zone, last_zone = self.zone_manager.select_next_and_last_zone(active_zones)
        required_export = self._compute_required_export(next_zone, mode=self.season_mode, band=self.outside_band)
        export = -self.ema_30s
        import_power = self.ema_5m

        # Store for sensors
        self.next_zone = next_zone
        self.last_zone = last_zone
        self.required_export = required_export
        self.export_margin = None if required_export is None else export - required_export

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
            await self.panic_manager.schedule_panic(active_zones)
            return

        # 10. Panic cooldown
        if self.panic_manager.is_in_cooldown(now_ts):
            self.last_action = "panic_cooldown"
            await self._log("[PANIC_COOLDOWN] skipping add/remove decisions")
            return

        # 11. ADD zone decision
        if next_zone and self.decision_engine.should_add_zone(next_zone, required_export):
            await self.action_executor.attempt_add_zone(next_zone, ac_power, export, required_export)
            return

        # 11. REMOVE zone decision
        if last_zone and self.decision_engine.should_remove_zone(last_zone, import_power, active_zones):
            await self.action_executor.attempt_remove_zone(last_zone, import_power)
            return

        # 12. SYSTEM BALANCED
        self.last_action = "balanced"
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

    def _compute_required_export(self, next_zone: str | None, mode: str | None = None, band: str | None = None) -> float | None:
        """Compute required export for the next zone using mode/band-aware learned power."""
        if not next_zone:
            return None

        zone_name = next_zone.split(".")[-1]
        lp = self.get_learned_power(zone_name, mode=mode or "default", band=band)
        return float(lp)

    def _read_zone_temps(self) -> None:
        """Read current temperatures for all configured zones from their sensors."""
        self.zone_current_temps = {}
        for zone_id, temp_sensor_id in self.zone_temp_sensors.items():
            if not temp_sensor_id:
                self.zone_current_temps[zone_id] = None
                continue
            
            st = self.hass.states.get(temp_sensor_id)
            if not st or st.state in ("unknown", "unavailable", ""):
                self.zone_current_temps[zone_id] = None
                continue
            
            try:
                self.zone_current_temps[zone_id] = float(st.state)
            except (TypeError, ValueError):
                self.zone_current_temps[zone_id] = None

    def _all_active_zones_at_target(self, active_zones: list[str]) -> bool:
        """
        Check if all active zones have reached their comfort targets.
        
        Returns True only if ALL active zones are at or above/below target:
        - In heat mode: all zones >= max_temp_winter
        - In cool mode: all zones <= min_temp_summer
        - In neutral mode: not applicable, returns False (allow normal removal)
        
        Returns False if any zone has no sensor or is below/above target.
        """
        if not active_zones or not self.season_mode:
            return False
        
        if self.season_mode == "neutral":
            return False
        
        for zone_id in active_zones:
            current_temp = self.zone_current_temps.get(zone_id)
            
            # Missing sensor: assume "not at target" (conservative, keeps zone on)
            if current_temp is None:
                return False
            
            if self.season_mode == "heat":
                if current_temp < self.max_temp_winter:
                    return False
            elif self.season_mode == "cool":
                if current_temp > self.min_temp_summer:
                    return False
        
        # All zones are at their targets
        return True

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
            _LOGGER.debug("Controller reset learning method failed or controller not set")

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
        """Master relay control based solely on solar production thresholds."""
        """Master relay control based solely on solar production thresholds."""
        ac_switch = self.config.get(CONF_AC_SWITCH)
        if not ac_switch:
            return

        try:
            on_threshold = float(self.config.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON))
        except (TypeError, ValueError):
            on_threshold = DEFAULT_SOLAR_THRESHOLD_ON

        try:
            off_threshold = float(self.config.get(CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF))
        except (TypeError, ValueError):
            off_threshold = DEFAULT_SOLAR_THRESHOLD_OFF

        switch_state_obj = self.hass.states.get(ac_switch)
        if not switch_state_obj:
            return

        switch_state = switch_state_obj.state

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
            # mark master_off_since for EMA reset logic
            self.master_off_since = dt_util.utcnow().timestamp()
            return
