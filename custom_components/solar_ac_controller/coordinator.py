# custom_components/solar_ac_controller/coordinator.py
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .actions import ActionExecutor
from .config_manager import ConfigManager
from .const import (
    CONF_AC_POWER_SENSOR,
    CONF_AC_SWITCH,
    CONF_ACTION_DELAY_SECONDS,
    CONF_ADD_CONFIDENCE,
    CONF_ENABLE_TEMP_MODULATION,
    CONF_GRID_SENSOR,
    CONF_INITIAL_LEARNED_POWER,
    CONF_MANUAL_LOCK_SECONDS,
    CONF_MAX_TEMP_WINTER,
    CONF_MIN_TEMP_SUMMER,
    CONF_PANIC_DELAY,
    CONF_PANIC_THRESHOLD,
    CONF_REMOVE_CONFIDENCE,
    CONF_SEASON_MODE,
    CONF_SHORT_CYCLE_OFF_SECONDS,
    CONF_SHORT_CYCLE_ON_SECONDS,
    CONF_SOLAR_SENSOR,
    CONF_SOLAR_THRESHOLD_OFF,
    CONF_SOLAR_THRESHOLD_ON,
    CONF_ZONES,
    DEFAULT_ACTION_DELAY_SECONDS,
    DEFAULT_ADD_CONFIDENCE,
    DEFAULT_ENABLE_TEMP_MODULATION,
    DEFAULT_INITIAL_LEARNED_POWER,
    DEFAULT_MANUAL_LOCK_SECONDS,
    DEFAULT_MAX_TEMP_WINTER,
    DEFAULT_MIN_TEMP_SUMMER,
    DEFAULT_PANIC_DELAY,
    DEFAULT_PANIC_THRESHOLD,
    DEFAULT_REMOVE_CONFIDENCE,
    DEFAULT_SEASON_MODE,
    DEFAULT_SHORT_CYCLE_OFF_SECONDS,
    DEFAULT_SHORT_CYCLE_ON_SECONDS,
    DEFAULT_SOLAR_THRESHOLD_OFF,
    DEFAULT_SOLAR_THRESHOLD_ON,
    DOMAIN,
)
from .decisions import DecisionEngine
from .exceptions import SensorInvalidError, SensorUnavailableError
from .metrics import MetricsCollector
from .panic import PanicManager
from .storage_circuit_breaker import StorageCircuitBreaker
from .zone_config_parser import ZoneConfigParser
from .zones import ZoneManager

# Type aliases for better readability
LearnedPowerData = Dict[str, Dict[str, float]]
ZoneMapping = Dict[str, str]
ZoneStates = Dict[str, Any]
ZoneLocks = Dict[str, Optional[float]]
SensorStates = Dict[str, Any]

_LOGGER = logging.getLogger(__name__)

_EMA_RESET_AFTER_OFF_SECONDS = 600


class SolarACCoordinator(DataUpdateCoordinator[SensorStates]):
    """Coordinator for Solar AC Controller integration."""

    note: str = ""  # Breadcrumb for diagnostics

    async def async_set_integration_enabled(self, enabled: bool) -> None:
        """Update and persist integration state."""
        self.integration_enabled = enabled
        await self._log(f"Integration {'enabled' if enabled else 'disabled'} by user.")
        self.stored_data["integration_enabled"] = enabled
        await self.store.async_save(self.stored_data)
        self.async_update_listeners()

    async def async_set_activity_logging_enabled(self, enabled: bool) -> None:
        """Toggle activity logging and persist state."""
        self.activity_logging_enabled = enabled
        await self._log(
            f"Activity logging {'enabled' if enabled else 'disabled'} by user."
        )
        self.stored_data["activity_logging_enabled"] = enabled

        if not self.storage_circuit_breaker.should_attempt_operation():
            _LOGGER.warning(
                "Storage circuit breaker open, skipping activity logging save"
            )
            return

        try:
            await self.store.async_save(self.stored_data)
            self.storage_circuit_breaker.record_success()
        except Exception as exc:
            _LOGGER.exception("Error saving activity logging state: %s", exc)
            self.storage_circuit_breaker.record_failure()
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
            name=DOMAIN,
            update_interval=timedelta(seconds=5),
        )

        # Basic initialization
        self.hass = hass
        self.config_entry = config_entry
        self.config_manager = ConfigManager(config_entry)
        self.config = self.config_manager.config
        self.store = store
        self.stored_data = stored or {}
        self.storage_circuit_breaker = StorageCircuitBreaker()
        self.metrics = MetricsCollector()
        self.version = version

        # Initialize runtime season_mode from stored data (with config fallback)
        self._season_mode = self.stored_data.get(
            "season_mode",
            self.config_manager.get(CONF_SEASON_MODE, DEFAULT_SEASON_MODE),
        )

        # Initialize integration state
        self.integration_enabled = self.stored_data.get("integration_enabled", True)
        self.activity_logging_enabled = self.stored_data.get(
            "activity_logging_enabled", False
        )

        # Initialize core components
        self._init_core_components()

        # Initialize configuration values
        self._init_config_values()

        # Initialize zone mappings
        self._init_zone_mappings()

        # Initialize learned data from storage
        self._init_learned_data(stored)

        # Initialize runtime state
        self._init_runtime_state()

        # Season mode (manual selection: heat or cool)

    @property
    def season_mode(self) -> str:
        # Check runtime value first, then stored data, then config
        if hasattr(self, "_season_mode"):
            return self._season_mode
        return self.stored_data.get("season_mode") or self.config_manager.get(
            CONF_SEASON_MODE, DEFAULT_SEASON_MODE
        )

    @season_mode.setter
    def season_mode(self, value: str):
        # Store runtime value (persistence handled separately)
        self._season_mode = value

    async def async_set_season_mode(self, value: str) -> None:
        """Set season mode and persist state."""
        self.season_mode = value
        self.stored_data["season_mode"] = value

        if not self.storage_circuit_breaker.should_attempt_operation():
            _LOGGER.warning("Storage circuit breaker open, skipping season mode save")
            return

        try:
            await self.store.async_save(self.stored_data)
            self.storage_circuit_breaker.record_success()
        except Exception as exc:
            _LOGGER.exception("Error saving season mode: %s", exc)
            self.storage_circuit_breaker.record_failure()
        self.async_update_listeners()

    def _init_runtime_state(self) -> None:
        """Initialize runtime state variables."""
        # Zone management state
        self.next_zone = None
        self.last_zone = None
        self.zone_last_changed = {}
        self.zone_last_changed_type = {}
        self.zone_last_state = {}
        self.zone_manual_lock_until = {}

        # Master AC control state
        self.master_last_state = None
        self.master_last_action_time = None
        self.master_manual_lock_state = None
        self.required_export = None
        self.export_margin = None
        self.master_off_since = None

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

        # Learning state
        self.last_action = None
        self.was_in_freeze = False  # Track previous freeze state for logging
        self.learning_active = False
        self.learning_start_time = None
        self.ac_power_before = None
        self.learning_zone = None
        self.ema_30s = 0.0
        self.ema_5m = 0.0

        # Defensive initialization
        self.required_export_source = "Initializing"

    def _init_core_components(self) -> None:
        """Initialize core component instances."""
        self.zone_manager = ZoneManager(self)
        self.panic_manager = PanicManager(self)
        self.decision_engine = DecisionEngine(self)
        self.action_executor = ActionExecutor(self)

    def _init_config_values(self) -> None:
        """Initialize configuration-derived values."""
        # Enable temperature modulation
        self.enable_temp_modulation = self.config_manager.get_bool(
            CONF_ENABLE_TEMP_MODULATION, DEFAULT_ENABLE_TEMP_MODULATION
        )

        # Comfort temperature targets (C)
        self.max_temp_winter = self.config_manager.get_float(
            CONF_MAX_TEMP_WINTER, DEFAULT_MAX_TEMP_WINTER
        )
        self.min_temp_summer = self.config_manager.get_float(
            CONF_MIN_TEMP_SUMMER, DEFAULT_MIN_TEMP_SUMMER
        )

        # Operational thresholds
        self.panic_threshold = self.config_manager.get_float(
            CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD
        )
        self.panic_delay = self.config_manager.get_int(
            CONF_PANIC_DELAY, DEFAULT_PANIC_DELAY
        )
        self.manual_lock_seconds = self.config_manager.get_int(
            CONF_MANUAL_LOCK_SECONDS, DEFAULT_MANUAL_LOCK_SECONDS
        )
        self.short_cycle_on_seconds = self.config_manager.get_int(
            CONF_SHORT_CYCLE_ON_SECONDS, DEFAULT_SHORT_CYCLE_ON_SECONDS
        )
        self.short_cycle_off_seconds = self.config_manager.get_int(
            CONF_SHORT_CYCLE_OFF_SECONDS, DEFAULT_SHORT_CYCLE_OFF_SECONDS
        )
        self.action_delay_seconds = self.config_manager.get_int(
            CONF_ACTION_DELAY_SECONDS, DEFAULT_ACTION_DELAY_SECONDS
        )
        self.add_confidence_threshold = self.config_manager.get_float(
            CONF_ADD_CONFIDENCE, DEFAULT_ADD_CONFIDENCE
        )
        self.remove_confidence_threshold = self.config_manager.get_float(
            CONF_REMOVE_CONFIDENCE, DEFAULT_REMOVE_CONFIDENCE
        )

        # Initial learned power
        self.initial_learned_power = self.config_manager.get_float(
            CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER
        )

    def _init_zone_mappings(self) -> None:
        """Initialize zone-related mappings."""
        zones_list = self.config_manager.get_list(CONF_ZONES, [])
        self.zone_temp_sensors = ZoneConfigParser.parse_temp_sensors(
            self.config_entry, zones_list
        )
        self.zone_manual_power = ZoneConfigParser.parse_manual_power(
            self.config_entry, zones_list
        )

    def _init_learned_data(self, stored: Optional[Dict[str, Any]]) -> None:
        """Initialize learned power data from storage."""
        stored = stored or {}
        raw_learned = stored.get("learned_power", {}) or {}
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

    # -------------------------------------------------------------------------
    # Helper accessors for learned_power (abstracts storage format)
    # -------------------------------------------------------------------------
    def get_learned_power(
        self,
        zone_name: str,
        mode: Optional[str] = None,
        band: Optional[str] = None,
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
        mode: Optional[str] = None,
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
        val = entry.get(
            mode or "default", entry.get("default", self.initial_learned_power)
        )
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

    async def async_persist_learned_values(self) -> None:
        """Persist learned values to storage."""
        if not self.storage_circuit_breaker.should_attempt_operation():
            _LOGGER.warning(
                "Storage circuit breaker open, skipping learned values save"
            )
            return

        try:
            payload = {
                "learned_power": self._rounded_power(self.learned_power),
                "samples": int(self.samples),
            }
            await self.store.async_save(payload)
            self.storage_circuit_breaker.record_success()
        except Exception as exc:
            _LOGGER.exception("Error saving learned values: %s", exc)
            self.storage_circuit_breaker.record_failure()
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
        try:
            # Keep this simple and non-blocking; expand if persistent logs are desired
            _LOGGER.info(
                message,
                extra={
                    "domain": DOMAIN,
                    "season_mode": getattr(self, "season_mode", None),
                    "cycle_count": getattr(self.metrics, "cycle_count", 0),
                    "integration_enabled": getattr(self, "integration_enabled", True),
                    "activity_logging": getattr(
                        self, "activity_logging_enabled", False
                    ),
                },
            )

            # Also fire event for activity logging if enabled
            if getattr(self, "activity_logging_enabled", False):
                try:
                    diagnostics_entity_id = (
                        f"sensor.{self.config_entry.entry_id}_diagnostics"
                    )
                    # Fire logbook entry event
                    self.hass.bus.async_fire(
                        "logbook_entry",
                        {
                            "name": "Solar AC Controller",
                            "message": message,
                            "domain": DOMAIN,
                            "entity_id": diagnostics_entity_id,
                        },
                    )
                except Exception:
                    _LOGGER.debug("Failed to fire activity log event")
        except Exception:
            _LOGGER.debug("Failed to write coordinator log message: %s", message)

    def _validate_sensor_state(self, state: Any, sensor_name: str) -> float:
        """Validate sensor state and return numeric value."""
        if not state or state.state in ("unknown", "unavailable"):
            raise SensorUnavailableError(f"{sensor_name} unavailable")
        try:
            return float(state.state)
        except (ValueError, TypeError) as e:
            raise SensorInvalidError(f"{sensor_name} invalid value: {e}")

    def _validate_configuration(self) -> None:
        """Validate configuration on startup."""
        from .exceptions import ConfigurationError

        required_sensors = [CONF_GRID_SENSOR, CONF_SOLAR_SENSOR, CONF_AC_POWER_SENSOR]

        for sensor in required_sensors:
            if not self.config_manager.get(sensor):
                raise ConfigurationError(f"Missing required sensor: {sensor}")

        zones = self.config_manager.get_list(CONF_ZONES, [])
        if not zones:
            raise ConfigurationError("At least one zone must be configured")

        # Validate zone temperature sensors exist if configured
        for zone, sensor in self.zone_temp_sensors.items():
            if sensor and not self.hass.states.get(sensor):
                _LOGGER.warning(f"Zone {zone} temperature sensor {sensor} not found")

    # -------------------------------------------------------------------------
    # Main update loop
    # -------------------------------------------------------------------------

    async def _async_update_data(self) -> None:
        """Main loop executed every 5 seconds."""
        cycle_start = self.metrics.record_cycle_start()

        try:
            # Integration enable/disable logic
            if hasattr(self, "integration_enabled") and not self.integration_enabled:
                self.last_action = "integration_disabled"
                self.note = "Integration disabled by user."
                _LOGGER.debug("Integration disabled, skipping all logic.")
                self.metrics.record_cycle_end(cycle_start, success=True)
                return

            # 1. Read sensors (grid, solar, ac_power)
            grid_raw = self._validate_sensor_state(
                self.hass.states.get(self.config_manager.get(CONF_GRID_SENSOR)),
                "Grid sensor",
            )
            solar = self._validate_sensor_state(
                self.hass.states.get(self.config_manager.get(CONF_SOLAR_SENSOR)),
                "Solar sensor",
            )
            ac_power = self._validate_sensor_state(
                self.hass.states.get(self.config_manager.get(CONF_AC_POWER_SENSOR)),
                "AC power sensor",
            )

            self.metrics.record_sensor_values(grid_raw, solar, ac_power)

            # Validate configuration on first run
            if not hasattr(self, "_config_validated"):
                self._validate_configuration()
                self._config_validated = True

            _LOGGER.debug(
                "Cycle sensors: grid_raw=%s solar=%s ac_power=%s",
                grid_raw,
                solar,
                ac_power,
            )

            # Enhanced logging with sensor values and calculations
            await self._log(
                f"[SENSORS] grid={round(grid_raw)}W solar={round(solar)}W ac_power={round(ac_power)}W "
                f"ema30s={round(self.ema_30s)}W ema5m={round(self.ema_5m)}W"
            )

            # EMA updates
            self._update_ema(grid_raw)

            # 2. Master switch auto-control (based ONLY on solar production)
            await self._handle_master_switch(solar, cycle_start)

            # 3. Freeze zone management when solar is too low (regardless of master switch state)
            # This must happen BEFORE any temperature/season reading to ensure complete freeze
            try:
                off_threshold = self.config_manager.get_float(
                    CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF
                )
            except (TypeError, ValueError):
                off_threshold = DEFAULT_SOLAR_THRESHOLD_OFF

            if solar <= off_threshold:
                # Ensure any running tasks are cancelled and learning reset
                await self._perform_freeze_cleanup()
                self.last_action = "solar_too_low"
                self.note = f"Solar {round(solar)}W <= threshold_off {off_threshold}W: freezing zone management."

                # Only log freeze entry, not every cycle
                if not self.was_in_freeze:
                    await self._log(
                        f"[FREEZE] solar={round(solar)}W <= threshold_off={off_threshold}W, "
                        f"freezing zone management"
                    )
                    self.was_in_freeze = True
                return

            # Reset freeze flag when exiting freeze mode
            if self.was_in_freeze:
                self.was_in_freeze = False

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
            next_zone, last_zone = self.zone_manager.select_next_and_last_zone(
                active_zones
            )
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

            # Enhanced logging for zone selection and calculations
            zone_info = f"active_zones={len(active_zones)}"
            if next_zone:
                zone_info += f" next_zone={next_zone}"
            if last_zone:
                zone_info += f" last_zone={last_zone}"
            if required_export is not None:
                zone_info += f" required_export={round(required_export)}W"
            zone_info += f" export={round(export)}W import_power={round(import_power)}W"

            await self._log(f"[ZONE_CALC] {zone_info}")

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

            # Enhanced logging for confidence calculations
            conf_info = f"add_conf={round(self.last_add_conf, 2)} remove_conf={round(self.last_remove_conf, 2)} "
            conf_info += f"confidence={round(self.confidence, 2)} "
            conf_info += f"add_threshold={round(self.add_confidence_threshold, 2)} "
            conf_info += (
                f"remove_threshold={round(self.remove_confidence_threshold, 2)}"
            )
            await self._log(f"[CONFIDENCE] {conf_info}")

            now_ts = dt_util.utcnow().timestamp()

            # 8. Learning timeout
            if self.learning_active and self.learning_start_time:
                if now_ts - self.learning_start_time >= 360:
                    await self._log(f"[LEARNING_TIMEOUT] zone={self.learning_zone}")
                    await self.controller.finish_learning()
                    return

            # 9. Panic logic
            if self.panic_manager.should_panic:
                self.note = "Panic triggered: grid import exceeded threshold with multiple zones active."
                await self.panic_manager.schedule_panic(active_zones)
                return

            # 10. Panic cooldown
            if self.panic_manager.is_in_cooldown:
                self.last_action = "panic_cooldown"
                # Calculate remaining cooldown time
                now_ts = dt_util.utcnow().timestamp()
                cooldown_remaining = max(0, 120 - (now_ts - (self.last_panic_ts or 0)))
                self.note = f"Panic cooldown active for {round(cooldown_remaining)}s: skipping add/remove decisions."
                await self._log(
                    f"[PANIC_COOLDOWN] active for {round(cooldown_remaining)}s, "
                    f"skipping add/remove decisions (active_zones={len(active_zones)})"
                )
                return

            # 11. ADD zone decision
            if next_zone and self.decision_engine.should_add_zone(
                next_zone, required_export if required_export is not None else 0.0
            ):
                zone_name = next_zone.split(".")[-1]
                learned_power = self.get_learned_power(zone_name, self.season_mode)
                reason = f"Adding zone {next_zone}: confidence={round(self.confidence, 2)} >= threshold={round(self.add_confidence_threshold, 2)}, "
                reason += f"export={round(export)}W >= required={round(required_export or 0)}W, "
                reason += f"learned_power={round(learned_power)}W"
                self.note = reason
                await self._log(f"[ADD_ZONE] {reason}")
                await self.action_executor.attempt_add_zone(
                    next_zone,
                    ac_power,
                    export,
                    required_export if required_export is not None else 0.0,
                )
                return

            # 11. REMOVE zone decision
            if last_zone and self.decision_engine.should_remove_zone(
                last_zone, import_power, active_zones
            ):
                zone_name = last_zone.split(".")[-1]
                learned_power = self.get_learned_power(zone_name, self.season_mode)
                reason = f"Removing zone {last_zone}: confidence={round(self.confidence, 2)} <= threshold={round(self.remove_confidence_threshold, 2)}, "
                reason += f"import_power={round(import_power)}W > 0W, "
                reason += f"learned_power={round(learned_power)}W, active_zones={len(active_zones)}"
                self.note = reason
                await self._log(f"[REMOVE_ZONE] {reason}")
                await self.action_executor.attempt_remove_zone(last_zone, import_power)
                return

            # 12. SYSTEM BALANCED
            self.last_action = "balanced"
            self.note = f"No action: system balanced. ema30={round(self.ema_30s)}, ema5m={round(self.ema_5m)}, zones={on_count}, samples={self.samples}"
            await self._log(
                f"[SYSTEM_BALANCED] ema30s={round(self.ema_30s)}W ema5m={round(self.ema_5m)}W "
                f"active_zones={on_count} confidence={round(self.confidence, 2)} samples={self.samples}"
            )
            self.metrics.record_cycle_end(cycle_start, success=True)
        except (SensorUnavailableError, SensorInvalidError) as e:
            # Sensor issues are expected during startup or temporary outages
            self.note = f"Sensor error: {e}"
            _LOGGER.warning("Sensor error in update cycle: %s", e)
            self.metrics.record_cycle_end(cycle_start, success=False)
        except Exception as e:
            self.note = f"Unexpected error in update cycle: {e}"
            _LOGGER.exception("Unexpected error in _async_update_data")
            self.metrics.record_cycle_end(cycle_start, success=False)

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
    async def _handle_master_switch(self, solar: float, cycle_start) -> None:
        """Master relay control with sticky manual lock until natural solar cycle aligns."""
        ac_switch = self.config_manager.get(CONF_AC_SWITCH)
        if not ac_switch:
            return

        try:
            on_threshold = self.config_manager.get_float(
                CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON
            )
        except (TypeError, ValueError):
            on_threshold = DEFAULT_SOLAR_THRESHOLD_ON

        try:
            off_threshold = self.config_manager.get_float(
                CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF
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
                f"[MASTER_ON] solar={round(solar)}W >= threshold_on={on_threshold}W, "
                f"turning AC master switch ON"
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
                f"[MASTER_OFF_TRIGGER] solar={round(solar)}W <= threshold_off={off_threshold}W, "
                f"turning AC master switch OFF"
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
            self.metrics.record_cycle_end(cycle_start, success=True)
            return
