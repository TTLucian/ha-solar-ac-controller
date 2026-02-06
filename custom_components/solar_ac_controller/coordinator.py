# custom_components/solar_ac_controller/coordinator.py
from __future__ import annotations

import asyncio
import copy
import logging
from datetime import timedelta
from typing import Any, Coroutine, Dict, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .actions import ActionExecutor
from .config_manager import ConfigManager
from .const import (
    CONF_AC_POWER_SENSOR,
    CONF_AC_SWITCH,
    CONF_ACTION_DELAY_SECONDS,
    CONF_AGGRESSIVENESS,
    CONF_COMPRESSOR_RAMP_SECONDS,
    CONF_ENABLE_TEMP_MODULATION,
    CONF_GRID_IMPORT_TOLERANCE,
    CONF_GRID_SENSOR,
    CONF_INITIAL_LEARNED_POWER,
    CONF_MANUAL_LOCK_SECONDS,
    CONF_MAX_TEMP_WINTER,
    CONF_MIN_TEMP_SUMMER,
    CONF_PANIC_DELAY,
    CONF_PANIC_THRESHOLD,
    CONF_SEASON_MODE,
    CONF_SHORT_CYCLE_OFF_SECONDS,
    CONF_SHORT_CYCLE_ON_SECONDS,
    CONF_SOLAR_SENSOR,
    CONF_SOLAR_THRESHOLD_OFF,
    CONF_UNIFIED_ADD_THRESHOLD,
    CONF_UNIFIED_REMOVE_THRESHOLD,
    CONF_UPDATE_INTERVAL,
    CONF_ZONES,
    DEFAULT_ACTION_DELAY_SECONDS,
    DEFAULT_AGGRESSIVENESS,
    DEFAULT_COMPRESSOR_RAMP_SECONDS,
    DEFAULT_ENABLE_TEMP_MODULATION,
    DEFAULT_GRID_IMPORT_TOLERANCE,
    DEFAULT_INITIAL_LEARNED_POWER,
    DEFAULT_MANUAL_LOCK_SECONDS,
    DEFAULT_MAX_TEMP_WINTER,
    DEFAULT_MIN_TEMP_SUMMER,
    DEFAULT_PANIC_DELAY,
    DEFAULT_PANIC_THRESHOLD,
    DEFAULT_SEASON_MODE,
    DEFAULT_SHORT_CYCLE_OFF_SECONDS,
    DEFAULT_SHORT_CYCLE_ON_SECONDS,
    DEFAULT_SOLAR_THRESHOLD_OFF,
    DEFAULT_UNIFIED_ADD_THRESHOLD,
    DEFAULT_UNIFIED_REMOVE_THRESHOLD,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    EMA_5M_ALPHA,
    EMA_10M_ALPHA,
    EMA_30S_ALPHA,
    EMA_RESET_AFTER_OFF_SECONDS,
    LEARNING_EMA_ALPHA,
    LEARNING_MAX_POWER_W,
    LEARNING_MIN_POWER_W,
    LEARNING_RELATIVE_TOLERANCE,
    LEARNING_TIMEOUT_SECONDS,
    PANIC_COOLDOWN_SECONDS,
    ZONE_SWAP_MIN_INTERVAL_SECONDS,
)
from .decisions import DecisionEngine
from .exceptions import SensorInvalidError, SensorUnavailableError, StorageError
from .helpers import EmaTracker, MasterSwitchController
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


class SolarACCoordinator(DataUpdateCoordinator[SensorStates]):
    """Coordinator for Solar AC Controller integration."""

    note: str = ""  # Breadcrumb for diagnostics

    async def async_set_integration_enabled(self, enabled: bool) -> None:
        """Update and persist integration state."""
        self.integration_enabled = enabled
        await self._log(
            f"Integration {'enabled' if enabled else 'disabled'} by user.", "info"
        )
        # Mutate stored_data under storage lock to avoid races when available
        _lock = getattr(self, "_storage_lock", None)
        if _lock is not None:
            async with _lock:
                self.stored_data["integration_enabled"] = enabled
                self._storage_dirty = True  # Mark as dirty
        else:
            self.stored_data["integration_enabled"] = enabled
            self._storage_dirty = True

        if not await self.storage_circuit_breaker.should_attempt_operation():
            _LOGGER.warning(
                "Storage circuit breaker open, skipping integration enabled save"
            )
            self.async_update_listeners()
            return

        try:
            await self._debounced_save()
        except (asyncio.CancelledError, OSError, ValueError) as exc:
            _LOGGER.exception(
                "Error scheduling integration enabled state save: %s", exc
            )
        self.async_update_listeners()

    async def async_set_activity_logging_enabled(self, enabled: bool) -> None:
        """Toggle activity logging and persist state."""
        self.activity_logging_enabled = enabled
        await self._log(
            f"Activity logging {'enabled' if enabled else 'disabled'} by user.", "info"
        )
        # Mutate stored_data under storage lock to avoid races when available
        _lock = getattr(self, "_storage_lock", None)
        if _lock is not None:
            async with _lock:
                self.stored_data["activity_logging_enabled"] = enabled
                self._storage_dirty = True  # Mark as dirty
        else:
            self.stored_data["activity_logging_enabled"] = enabled
            self._storage_dirty = True

        if not await self.storage_circuit_breaker.should_attempt_operation():
            _LOGGER.warning(
                "Storage circuit breaker open, skipping activity logging save"
            )
            # Even if we can't persist, notify listeners of the in-memory change
            self.async_update_listeners()
            return

        try:
            await self._debounced_save()
        except (asyncio.CancelledError, OSError, ValueError) as exc:
            _LOGGER.exception("Error scheduling activity logging state save: %s", exc)
        self.async_update_listeners()

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: Any,
        store: Any,
        stored: dict[str, Any] | None,
        version: str | None = None,
    ) -> None:

        # Basic initialization
        self.hass = hass
        self.config_entry = config_entry
        self.config_manager = ConfigManager(config_entry)
        self.config = self.config_manager.config

        # Get update interval from config (default 10 seconds)
        update_interval_seconds = self.config_manager.get_int(
            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        )

        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval_seconds),
        )
        self.store = store
        self.stored_data = stored or {}
        self.storage_circuit_breaker = StorageCircuitBreaker()
        # Set coordinator reference for logging (avoiding circular import)
        self.storage_circuit_breaker.coordinator = self
        self.metrics = MetricsCollector()
        self.version = version

        # Storage debouncing
        self._storage_debounce_task: asyncio.Task | None = None
        self._last_storage_save: float = 0.0
        self._storage_debounce_seconds = (
            5.0  # Minimum 5 seconds between saves (increased)
        )
        self._storage_lock = asyncio.Lock()
        self._update_lock = asyncio.Lock()
        self._storage_dirty = False  # Track if data has actually changed

        # State lookup cache for performance
        self._state_cache: Dict[str, Any] = {}
        self._cache_timestamp = 0.0

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

        # Initialize learned data from storage
        self._init_learned_data(stored)

        # Initialize core components
        self._init_core_components()

        # Initialize configuration values
        self._init_config_values()

        # Initialize zone mappings
        self._init_zone_mappings()

        # Validate configuration once during initialization (after zone mappings are set up)
        self._validate_configuration()
        self._config_validated = True

        # Initialize runtime state
        self._init_runtime_state()

        # Flag to log configuration validation on first update
        self._config_validation_logged = False

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
    def season_mode(self, value: str) -> None:
        # Store runtime value (persistence handled separately)
        self._season_mode = value

    @property
    def learning_active(self) -> bool:
        """Check if learning is currently active."""
        return (
            getattr(self.controller.session, "_active", False)
            if hasattr(self, "controller")
            else False
        )

    async def async_set_season_mode(self, value: str) -> None:
        """Set season mode and persist state."""
        self.season_mode = value
        # Mutate stored_data under storage lock to avoid races when available
        _lock = getattr(self, "_storage_lock", None)
        if _lock is not None:
            async with _lock:
                self.stored_data["season_mode"] = value
                self._storage_dirty = True  # Mark as dirty
        else:
            self.stored_data["season_mode"] = value
            self._storage_dirty = True

        if not await self.storage_circuit_breaker.should_attempt_operation():
            _LOGGER.warning("Storage circuit breaker open, skipping season mode save")
            return

        try:
            await self._debounced_save()
        except (asyncio.CancelledError, OSError, ValueError) as exc:
            _LOGGER.exception("Error scheduling season mode save: %s", exc)
        self.async_update_listeners()

    async def async_set_aggressiveness(self, value: float) -> None:
        """Set aggressiveness and persist to config entry options."""
        try:
            self.aggressiveness = float(value)
        except (TypeError, ValueError):
            return

        # Persist into config entry options so OptionsFlow and UI reflect change
        try:
            new_options = {
                **getattr(self.config_entry, "options", {}),
                CONF_AGGRESSIVENESS: float(self.aggressiveness),
            }
            # Use hass.config_entries to update options asynchronously
            self.hass.config_entries.async_update_entry(
                self.config_entry, options=new_options
            )
        except Exception as exc:  # pragma: no cover - defensive
            _LOGGER.exception("Failed to persist aggressiveness option: %s", exc)

        # Notify listeners so entity states refresh
        self.async_update_listeners()

    def _init_runtime_state(self) -> None:
        """Initialize runtime state variables."""
        # Zone management state
        self.next_zone = None
        self.last_zone = None

        # Performance optimization counters
        self._cycle_counter = 0
        self._last_sensor_log_cycle = 0
        self.zone_last_changed: dict[str, float] = {}
        self.zone_last_changed_type: dict[str, str] = {}
        self.zone_last_state: dict[str, str] = {}
        self.zone_manual_lock_until: dict[str, float] = {}

        # Master AC control state
        self.master_last_state: str | None = None
        self.master_last_action_time: float | None = None
        self.master_manual_lock_state: str | None = None
        self.required_export: float | None = None
        self.export_margin: float | None = None
        self.master_off_since: float | None = None

        # Controller and confidence tracking
        from .controller import SolarACController

        self.controller = SolarACController(self.hass, self, self.store)
        self.last_add_conf = 0.0
        self.last_remove_conf = 0.0
        self.confidence = 0.0
        self.last_action_start_ts: float | None = None
        self.last_action_duration: float | None = None
        self._panic_task: Optional[asyncio.Task[None]] = None
        self.last_panic_ts: float | None = None

        # Learning state
        self.last_action: str | None = None
        self.was_in_freeze = False  # Track previous freeze state for logging
        self.learning_start_time: float | None = None
        self.ac_power_before: float | None = None
        self.learning_zone: str | None = None
        self.ema_30s = 0.0
        self.ema_5m = 0.0
        # Compressor recovery timestamp (unix ts) - prevents rapid re-add until compressor ramps
        self.compressor_recover_until = 0.0

        # Cached learning active flag for synchronous/lock-free reads
        self.learning_active_cached = False

        # Per-decision diagnostic breakdowns (populated by DecisionEngine)
        self.last_add_breakdown: dict = {}
        self.last_remove_breakdown: dict = {}

        # Temperature stability tracking for zone swapping
        self.temp_ema_10m: dict[str, float] = {}  # zone -> 10min EMA temperature
        self.zone_last_swap_time: dict[str, float] = {}  # zone -> last swap timestamp

        # Defensive initialization
        self.required_export_source = "Initializing"

        # Sensor recovery tracking
        self._sensor_unavailable_since: Dict[str, float] = (
            {}
        )  # sensor_id -> timestamp when it became unavailable

    async def _log_configuration_validation(self) -> None:
        """Log configuration validation results during startup."""
        try:
            # Validate zones
            zones = self.config.get(CONF_ZONES, [])
            valid_zones = []
            invalid_zones = []

            for zone in zones:
                if not zone or not isinstance(zone, str):
                    invalid_zones.append(str(zone))
                    continue

                # Check if zone entity exists
                state_obj = self.hass.states.get(zone)
                if state_obj:
                    valid_zones.append(zone)
                else:
                    invalid_zones.append(zone)

            # Validate sensors
            sensors_to_check = [
                (CONF_SOLAR_SENSOR, "solar sensor"),
                (CONF_GRID_SENSOR, "grid sensor"),
                (CONF_AC_SWITCH, "AC switch"),
            ]

            valid_sensors = []
            invalid_sensors = []

            for sensor_key, sensor_desc in sensors_to_check:
                sensor_id = self.config.get(sensor_key)
                if sensor_id:
                    state_obj = self.hass.states.get(sensor_id)
                    if state_obj is not None:
                        valid_sensors.append(f"{sensor_desc} ({sensor_id})")
                    else:
                        invalid_sensors.append(f"{sensor_desc} ({sensor_id})")

            # Log configuration summary
            await self._log(
                f"Configuration validated: {len(valid_zones)} zones configured, "
                f"{len(valid_sensors)} sensors connected, "
                f"operating in {self.season_mode} mode, "
                f"emergency threshold at {self.panic_threshold}W, "
                f"zone activation confidence threshold at {self.unified_add_threshold}",
                "info",
            )

            # Log details of invalid configurations
            if invalid_zones:
                await self._log(
                    f"Warning: {len(invalid_zones)} zone(s) not found in Home Assistant: {', '.join(invalid_zones)}",
                    "warning",
                )

            if invalid_sensors:
                await self._log(
                    f"Warning: {len(invalid_sensors)} sensor(s) not available: {', '.join(invalid_sensors)}",
                    "warning",
                )

            if valid_zones:
                await self._log(
                    f"Active zones configured: {', '.join(valid_zones)}", "info"
                )

        except Exception as e:
            await self._log(f"Configuration validation failed: {str(e)}", "error")

    async def _log_decision_reasoning(
        self, decision_type: str, reasoning: str, **kwargs
    ) -> None:
        """Log detailed decision reasoning (optional verbose logging)."""
        # Only log if activity logging is enabled and we want verbose decision logging
        if not getattr(self, "activity_logging_enabled", False):
            return

        # Check if verbose decision logging is enabled (could be a future config option)
        verbose_decisions = getattr(self, "_verbose_decision_logging", False)
        if not verbose_decisions:
            return

        details = " ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None)
        await self._log(
            f"[DECISION_REASONING] type={decision_type} reasoning={reasoning} {details}",
            "debug",
        )

    def _init_core_components(self) -> None:
        """Initialize core component instances."""
        self.zone_manager = ZoneManager(self)
        self.panic_manager = PanicManager(self)
        self.decision_engine = DecisionEngine(self)
        self.action_executor = ActionExecutor(self)
        self.ema_tracker = EmaTracker(EMA_30S_ALPHA, EMA_5M_ALPHA)
        self.master_controller = MasterSwitchController(self)

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
        self.unified_add_threshold = self.config_manager.get_float(
            CONF_UNIFIED_ADD_THRESHOLD, DEFAULT_UNIFIED_ADD_THRESHOLD
        )
        self.unified_remove_threshold = self.config_manager.get_float(
            CONF_UNIFIED_REMOVE_THRESHOLD, DEFAULT_UNIFIED_REMOVE_THRESHOLD
        )

        # Grid import tolerance used when allowing adds (positive import allowed)
        self.grid_import_tolerance = self.config_manager.get_float(
            CONF_GRID_IMPORT_TOLERANCE, DEFAULT_GRID_IMPORT_TOLERANCE
        )

        # Compressor recovery and aggressiveness tuning
        self.compressor_ramp_seconds = self.config_manager.get_int(
            CONF_COMPRESSOR_RAMP_SECONDS, DEFAULT_COMPRESSOR_RAMP_SECONDS
        )
        # Aggressiveness: 0.0 conservative -> 1.0 aggressive
        self.aggressiveness = float(
            self.config_manager.get_float(CONF_AGGRESSIVENESS, DEFAULT_AGGRESSIVENESS)
        )

        # Initial learned power
        self.initial_learned_power = self.config_manager.get_float(
            CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER
        )

        # Configuration validation moved to _init_core_components after zone mappings

    def _init_zone_mappings(self) -> None:
        """Initialize zone-related mappings."""
        zones_list = self.config_manager.get_list(CONF_ZONES, [])
        self.zone_temp_sensors = ZoneConfigParser.parse_temp_sensors(
            self.config_entry, zones_list
        )
        self.zone_manual_power = ZoneConfigParser.parse_manual_power(
            self.config_entry, zones_list
        )

        # Initialize zone priorities based on config order (first = highest priority)
        self.zone_priorities = {}
        for i, zone in enumerate(zones_list):
            zone_name = zone.split(".")[-1]
            self.zone_priorities[zone_name] = i

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
                        except (ValueError, TypeError):
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
        except (ValueError, TypeError):
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

        # Validate zone_name against configured zones
        all_zones = self.config.get(CONF_ZONES, [])
        zone_names = [z.split(".")[-1] for z in all_zones]

        if zone_name not in zone_names:
            _LOGGER.warning(
                "Attempted to set learned power for unconfigured zone: %s. "
                "Configured zones: %s",
                zone_name,
                zone_names,
            )
            return

        # Validate mode string
        valid_modes = {"default", "heat", "cool"}
        if mode and mode not in valid_modes:
            _LOGGER.warning(
                "Attempted to set learned power with invalid mode: %s. "
                "Valid modes: %s",
                mode,
                valid_modes,
            )
            return

        # Reasonable absolute bounds for a single zone incremental draw (W)
        MIN_W = LEARNING_MIN_POWER_W
        MAX_W = LEARNING_MAX_POWER_W
        # Relative tolerance around existing learned value (± fraction)
        REL_TOL = (
            LEARNING_RELATIVE_TOLERANCE  # accept within ±50% of current learned value
        )
        # Smoothing factor for EMA update
        ALPHA = LEARNING_EMA_ALPHA

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
            _LOGGER.debug(
                "Discarding outlier sample for %s: %sW outside [%s,%s]",
                zone_name,
                new_sample,
                MIN_W,
                MAX_W,
            )
            return

        # Relative outlier filter (only apply if we have a meaningful current value)
        lower = max(MIN_W, current * (1.0 - REL_TOL))
        upper = min(MAX_W, current * (1.0 + REL_TOL))
        if not (lower <= new_sample <= upper):
            _LOGGER.debug(
                "Discarding relative outlier for %s: %sW outside [%s,%s] around current %sW",
                zone_name,
                new_sample,
                round(lower, 1),
                round(upper, 1),
                round(current, 1),
            )
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
        if not await self.storage_circuit_breaker.should_attempt_operation():
            _LOGGER.warning(
                "Storage circuit breaker open, skipping learned values save"
            )
            return

        try:
            # Update stored_data under lock so readers/savers don't race
            async with self._storage_lock:
                self.stored_data["learned_power"] = self._rounded_power(
                    self.learned_power
                )
                self.stored_data["samples"] = int(self.samples)
                self._storage_dirty = True
            await self._debounced_save()
        except (asyncio.CancelledError, OSError, ValueError) as exc:
            _LOGGER.exception("Error scheduling learned values save: %s", exc)

    def _rounded_power(self, value: Any) -> Any:
        """Recursively round power values to whole watts for clean storage."""
        if isinstance(value, dict):
            return {k: self._rounded_power(v) for k, v in value.items()}
        try:
            return int(round(float(value)))
        except (ValueError, TypeError):
            return value

    # -------------------------------------------------------------------------
    # Minimal async logging hook used by coordinator and controller
    # -------------------------------------------------------------------------
    async def _log(self, message: str, level: str = "info") -> None:
        """Async logging hook used by coordinator and controller.

        Args:
            message: The log message
            level: Log level for activity logging ("debug", "info", "warning", "error")
        """
        try:
            # Prepare extra data once
            extra_data = {
                "domain": DOMAIN,
                "season_mode": getattr(self, "season_mode", None),
                "cycle_count": getattr(self.metrics, "cycle_count", 0),
                "integration_enabled": getattr(self, "integration_enabled", True),
                "activity_logging": getattr(self, "activity_logging_enabled", False),
            }

            # Map textual level to logger method for system logs
            level_lower = (level or "info").lower()
            logger_map = {
                "debug": _LOGGER.debug,
                "info": _LOGGER.info,
                "warning": _LOGGER.warning,
                "error": _LOGGER.error,
            }
            logger_method = logger_map.get(level_lower, _LOGGER.info)

            # Log to system logger using requested level (includes extra context)
            try:
                logger_method(message, extra=extra_data)
            except Exception:
                # Fallback to info if specific logger call fails
                _LOGGER.info(message, extra=extra_data)

            # Activity logging (only when enabled) - emit to logbook but throttle repeated messages
            if getattr(self, "activity_logging_enabled", False):
                try:
                    # Lazy-init throttle settings
                    if not hasattr(self, "_logbook_throttle_seconds"):
                        self._logbook_throttle_seconds = 3.0
                    if not hasattr(self, "_last_logbook_emit"):
                        self._last_logbook_emit: dict[str, float] = {}

                    # Resolve the actual diagnostics entity id via the entity registry
                    # to ensure logbook entries are associated with the correct entity.
                    diagnostics_entity_id = None
                    try:
                        from homeassistant.helpers import entity_registry as er

                        registry = er.async_get(self.hass)
                        # Unique id expected to match '<entry_id>_diagnostics'
                        unique_id = f"{self.config_entry.entry_id}_diagnostics"
                        reg_entry = registry.async_get_entity_id(
                            "sensor", DOMAIN, unique_id
                        )
                        if reg_entry:
                            diagnostics_entity_id = reg_entry
                    except Exception:
                        # Fall back to constructed id if registry lookup fails
                        diagnostics_entity_id = (
                            f"sensor.{self.config_entry.entry_id}_diagnostics"
                        )

                    # Map level to logbook level string
                    level_map = {
                        "debug": "DEBUG",
                        "info": "INFO",
                        "warning": "WARNING",
                        "error": "ERROR",
                    }
                    logbook_level = level_map.get(level_lower, "INFO")

                    # Enhanced message with context for better diagnostics
                    enhanced_message = f"[{logbook_level}] {message}"

                    # Throttle repeated identical messages to avoid logbook spam
                    now_ts = dt_util.utcnow().timestamp()
                    key = f"{logbook_level}:{message}"
                    last = self._last_logbook_emit.get(key, 0.0)
                    if now_ts - last >= float(self._logbook_throttle_seconds):
                        payload = {
                            "name": "Solar AC Controller",
                            "message": enhanced_message,
                            "domain": DOMAIN,
                            "level": logbook_level,
                        }
                        if diagnostics_entity_id:
                            payload["entity_id"] = diagnostics_entity_id
                        self.hass.bus.async_fire("logbook_entry", payload)
                        self._last_logbook_emit[key] = now_ts
                except (ValueError, TypeError, AttributeError):
                    # Silent failure for activity logging
                    pass
        except (ValueError, TypeError, AttributeError):
            # Silent failure for main logging to avoid recursive errors
            pass

    async def _debounced_save(self) -> None:
        """Debounced storage save to prevent excessive I/O."""
        # Early return if no changes to save
        if not self._storage_dirty:
            return

        async with self._storage_lock:
            now = dt_util.utcnow().timestamp()
            time_since_last_save = now - self._last_storage_save

            if time_since_last_save < self._storage_debounce_seconds:
                # Cancel existing task if it's still pending
                if (
                    self._storage_debounce_task
                    and not self._storage_debounce_task.done()
                ):
                    self._storage_debounce_task.cancel()
                    try:
                        await self._storage_debounce_task
                    except asyncio.CancelledError:
                        pass
                    finally:
                        self._storage_debounce_task = None  # Clean up reference

                # Schedule new save
                delay = self._storage_debounce_seconds - time_since_last_save
                # Schedule delayed save on Home Assistant's loop (use safe wrapper)
                try:
                    self._storage_debounce_task = self.create_task(
                        self._delayed_save(delay)
                    )
                except Exception:
                    self._storage_debounce_task = self.hass.async_create_task(
                        self._delayed_save(delay)
                    )
                return

            # Save immediately if enough time has passed
            await self._perform_storage_save()
            self._last_storage_save = now

    async def _delayed_save(self, delay: float) -> None:
        """Perform delayed storage save."""
        try:
            await asyncio.sleep(delay)
            async with self._storage_lock:
                await self._perform_storage_save()
                self._last_storage_save = dt_util.utcnow().timestamp()
        except asyncio.CancelledError:
            # Save was cancelled due to new save request
            pass
        finally:
            # Clean up task reference
            if self._storage_debounce_task and self._storage_debounce_task.done():
                self._storage_debounce_task = None

    async def _flush_pending_storage_save(self) -> None:
        """Flush any pending debounced storage save."""
        if self._storage_debounce_task and not self._storage_debounce_task.done():
            self._storage_debounce_task.cancel()
            try:
                await self._storage_debounce_task
            except asyncio.CancelledError:
                pass
            finally:
                self._storage_debounce_task = None  # Clean up reference
            # Perform immediate save
            async with self._storage_lock:
                await self._perform_storage_save()
                self._last_storage_save = dt_util.utcnow().timestamp()

    async def _perform_storage_save(self) -> None:
        """Perform the actual storage save operation."""
        if not await self.storage_circuit_breaker.should_attempt_operation():
            _LOGGER.warning("Storage circuit breaker open, skipping save")
            return

        try:
            # Copy stored_data to avoid races where other coroutines mutate
            # the in-memory dict while the I/O operation is in progress.
            # Use deepcopy to be safe for nested structures.
            data_to_save = copy.deepcopy(self.stored_data)
            await self.store.async_save(data_to_save)
            await self.storage_circuit_breaker.record_success()
            self._storage_dirty = False  # Clear dirty flag on successful save
        except (OSError, StorageError) as exc:
            _LOGGER.exception("Error saving to storage: %s", exc)
            await self.storage_circuit_breaker.record_failure()

    def _get_cached_state(self, entity_id: str) -> Any:
        """Get entity state with caching for performance."""
        now = dt_util.utcnow().timestamp()

        # Invalidate cache every update cycle (every few seconds)
        if now - self._cache_timestamp > 1.0:
            self._state_cache.clear()
            self._cache_timestamp = now

        if entity_id not in self._state_cache:
            self._state_cache[entity_id] = self.hass.states.get(entity_id)

        return self._state_cache[entity_id]

    def _validate_sensor_state(self, state: Any, sensor_name: str) -> float:
        """Validate sensor state and return numeric value."""
        if not state or state.state in ("unknown", "unavailable"):
            # Track when sensor became unavailable
            now = dt_util.utcnow().timestamp()
            if sensor_name not in self._sensor_unavailable_since:
                self._sensor_unavailable_since[sensor_name] = now
            raise SensorUnavailableError(f"{sensor_name} unavailable")

        # Sensor is available - check if it was previously unavailable
        if sensor_name in self._sensor_unavailable_since:
            # Sensor recovered - log the recovery
            unavailable_duration = (
                dt_util.utcnow().timestamp()
                - self._sensor_unavailable_since[sensor_name]
            )
            # Use safe task creation helper to ensure exceptions are logged
            try:
                self.create_task(
                    self._log_sensor_recovery(sensor_name, unavailable_duration)
                )
            except Exception:
                # Fallback to direct task creation if helper isn't available
                try:
                    self.hass.async_create_task(
                        self._log_sensor_recovery(sensor_name, unavailable_duration)
                    )
                except Exception:
                    pass
            del self._sensor_unavailable_since[sensor_name]

        try:
            return float(state.state)
        except (ValueError, TypeError) as e:
            raise SensorInvalidError(f"{sensor_name} invalid value: {e}")

    async def _log_sensor_recovery(self, sensor_name: str, duration: float) -> None:
        """Log sensor recovery event."""
        await self._log(
            f"Sensor '{sensor_name}' recovered after being unavailable for {round(duration, 1)} seconds",
            "info",
        )

    def create_task(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Create a background task and ensure exceptions are logged.

        Use this instead of calling `hass.async_create_task` directly to
        attach a done-callback which logs unhandled exceptions.
        """
        task = self.hass.async_create_task(coro)

        def _done_callback(t: asyncio.Task) -> None:
            try:
                exc = t.exception()
                if exc:
                    _LOGGER.exception("Background task exception: %s", exc)
            except asyncio.CancelledError:
                # Cancellation is not an error to report
                pass

        try:
            task.add_done_callback(_done_callback)
        except Exception:
            # Defensive: ignore if task can't accept callbacks
            pass
        return task

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
        async with self._update_lock:
            cycle_start = self.metrics.record_cycle_start()

            # Log configuration validation on first run
            if not self._config_validation_logged:
                await self._log_configuration_validation()
                self._config_validation_logged = True

            try:
                # Integration enable/disable logic
                if (
                    hasattr(self, "integration_enabled")
                    and not self.integration_enabled
                ):
                    self.last_action = "integration_disabled"
                    self.note = "Integration disabled by user."
                    _LOGGER.debug("Integration disabled, skipping all logic.")
                    self.metrics.record_cycle_end(cycle_start, success=True)
                    return

                # 1. Read sensors (grid, solar, ac_power)
                grid_raw = self._validate_sensor_state(
                    self._get_cached_state(self.config_manager.get(CONF_GRID_SENSOR)),
                    "Grid sensor",
                )
                solar = self._validate_sensor_state(
                    self._get_cached_state(self.config_manager.get(CONF_SOLAR_SENSOR)),
                    "Solar sensor",
                )
                ac_power = self._validate_sensor_state(
                    self._get_cached_state(
                        self.config_manager.get(CONF_AC_POWER_SENSOR)
                    ),
                    "AC power sensor",
                )

                self.metrics.record_sensor_values(grid_raw, solar, ac_power)

                _LOGGER.debug(
                    "Cycle sensors: grid_raw=%s solar=%s ac_power=%s",
                    grid_raw,
                    solar,
                    ac_power,
                )

                # Enhanced logging with sensor values and calculations (every minute)
                self._cycle_counter += 1
                if (
                    self._cycle_counter - self._last_sensor_log_cycle >= 6
                ):  # Every ~60 seconds
                    await self._log(
                        f"[SENSORS] grid={round(grid_raw)}W solar={round(solar)}W ac_power={round(ac_power)}W "
                        f"ema30s={round(self.ema_30s)}W ema5m={round(self.ema_5m)}W",
                        "debug",
                    )
                    self._last_sensor_log_cycle = self._cycle_counter

                # EMA updates
                self._update_ema(grid_raw)

                # 2. Master switch auto-control (based ONLY on solar production)
                await self.master_controller.handle_master_switch(solar, cycle_start)

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
                    _LOGGER.error(
                        "zone_manager is not initialized! Skipping update cycle."
                    )
                    self.last_action = "zone_manager_uninitialized"
                    return

                active_zones = (
                    await self.zone_manager.update_zone_states_and_overrides()
                )
                on_count = len(active_zones)
                self.on_count = on_count  # Set for panic manager

                # Store active zones for decision engine
                self.active_zones = active_zones

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
                except (ValueError, TypeError, KeyError, AttributeError):
                    self.required_export_source = "Learned Power"
                self.export_margin = (
                    None if required_export is None else export - required_export
                )

                # Enhanced logging for zone selection and calculations
                zone_info = f"active_zones={len(active_zones)}"
                if next_zone:
                    next_zone_name = next_zone.split(".")[-1]
                    next_power = self.get_learned_power(
                        next_zone_name, self.season_mode
                    )
                    zone_info += f" next_zone={next_zone}({round(next_power)}W)"
                if last_zone:
                    last_zone_name = last_zone.split(".")[-1]
                    last_power = self.get_learned_power(
                        last_zone_name, self.season_mode
                    )
                    zone_info += f" last_zone={last_zone}({round(last_power)}W)"
                if required_export is not None:
                    zone_info += f" required_export={round(required_export)}W"
                zone_info += f" export={round(export)}W import_power={round(import_power)}W season_mode={self.season_mode}"

                await self._log(f"[ZONE_CALC] {zone_info}", "debug")

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
                # Treat remove confidence as a positive "removal pressure" value.
                # Negative values from compute_remove_conf (e.g., due to short-cycle
                # penalties) should not *increase* add confidence, so clamp to zero.
                remove_pressure = max(0.0, self.last_remove_conf)
                self.confidence = self.last_add_conf - remove_pressure

                # Enhanced logging for confidence calculations
                conf_info = f"unified_conf={round(self.confidence, 2)} "
                conf_info += f"(add={round(self.last_add_conf, 2)}, remove={round(self.last_remove_conf, 2)}) "
                conf_info += f"thresholds(add≥{round(self.unified_add_threshold, 2)}, remove≤{round(self.unified_remove_threshold, 2)}) "

                # Determine decision state for clarity
                if self.confidence >= self.unified_add_threshold:
                    decision_state = "ADD_READY"
                elif self.confidence <= self.unified_remove_threshold:
                    decision_state = "REMOVE_READY"
                else:
                    decision_state = "STABLE"

                conf_info += f"→ {decision_state}"
                if next_zone:
                    conf_info += f"next_candidate={next_zone.split('.')[-1]} "
                if last_zone:
                    conf_info += f"last_candidate={last_zone.split('.')[-1]}"
                await self._log(f"[CONFIDENCE] {conf_info}", "debug")

                now_ts = dt_util.utcnow().timestamp()

                # 8. Learning timeout
                learning_zone = await self.controller.session.get_zone()
                learning_start_time = await self.controller.session.get_start_time()
                # Update cached learning flag for synchronous checks in DecisionEngine
                try:
                    self.learning_active_cached = bool(learning_zone)
                except Exception:
                    self.learning_active_cached = False
                if (
                    learning_zone
                    and learning_start_time
                    and now_ts - learning_start_time >= LEARNING_TIMEOUT_SECONDS
                ):
                    await self._log(f"[LEARNING_TIMEOUT] zone={learning_zone}", "info")
                    result = await self.controller.finish_learning()
                    if not result.success:
                        await self._log(
                            f"Learning failed: {result.error_message}", "warning"
                        )
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
                    cooldown_remaining = max(
                        0,
                        PANIC_COOLDOWN_SECONDS - (now_ts - (self.last_panic_ts or 0)),
                    )
                    self.note = (
                        f"Panic cooldown active for {round(cooldown_remaining)}s: "
                        "skipping add/remove decisions."
                    )
                    await self._log(
                        f"[PANIC_COOLDOWN] active for {round(cooldown_remaining)}s, "
                        f"skipping add/remove decisions (active_zones={len(active_zones)})"
                    )
                    return

                # 11. ADD zone decision
                if next_zone and await self.decision_engine.should_add_zone(
                    next_zone, required_export if required_export is not None else 0.0
                ):
                    # Single zone addition (normal case)

                    # Single zone addition (normal case)
                    zone_name = next_zone.split(".")[-1]
                    learned_power = self.get_learned_power(zone_name, self.season_mode)
                    reason = f"Activating zone '{zone_name}' - "
                    reason += f"confidence score {round(self.confidence, 1)} meets activation threshold, "
                    reason += f"excess solar power {round(export)}W available, "
                    reason += f"zone requires {round(learned_power)}W, "
                    reason += f"currently {len(active_zones)} zones active"
                    self.note = f"Adding zone {next_zone}: confidence {round(self.confidence, 2)} >= {round(self.unified_add_threshold, 2)}"
                    await self._log(reason, "info")
                    await self.action_executor.attempt_add_zone(
                        next_zone,
                        ac_power,
                        export,
                        required_export if required_export is not None else 0.0,
                    )
                    return

                # 11. REMOVE zone decision
                if last_zone and await self.decision_engine.should_remove_zone(
                    last_zone, import_power, active_zones
                ):
                    zone_name = last_zone.split(".")[-1]
                    learned_power = self.get_learned_power(zone_name, self.season_mode)
                    reason = f"Removing zone {last_zone} ({zone_name}): "
                    reason += f"unified_conf={round(self.confidence, 2)} <= remove_threshold={round(self.unified_remove_threshold, 2)}, "
                    reason += f"import_power={round(import_power)}W > 0W, "
                    reason += f"learned_power={round(learned_power)}W, "
                    reason += f"current_grid={round(self.ema_30s)}W, active_zones={len(active_zones)}, season_mode={self.season_mode}"
                    self.note = f"Removing zone {last_zone}: unified_conf={round(self.confidence, 2)} <= {round(self.unified_remove_threshold, 2)}"
                    await self._log(f"[REMOVE_ZONE] {reason}")
                    await self.action_executor.attempt_remove_zone(
                        last_zone, import_power
                    )
                    return

                # 11.5. ZONE SWAP decision (only when no net add/remove needed)
                # Sort active zones by reverse priority (remove lowest priority satisfied zones first)
                for active_zone in sorted(
                    active_zones,
                    key=lambda z: self.zone_priorities.get(z.split(".")[-1], 999),
                    reverse=True,
                ):
                    zone_to_add = await self.decision_engine.should_swap_zone(
                        active_zone, import_power
                    )
                    if zone_to_add:
                        await self._perform_zone_swap(active_zone, zone_to_add)
                        return

                # 12. SYSTEM BALANCED
                self.last_action = "balanced"
                self.note = f"No action: system balanced. ema30={round(self.ema_30s)}, ema5m={round(self.ema_5m)}, zones={on_count}, samples={self.samples}"

                # Log balanced state every 10 minutes (600 seconds) to avoid spam
                now_ts = dt_util.utcnow().timestamp()
                last_balanced_log = getattr(self, "_last_balanced_log_time", 0)
                if now_ts - last_balanced_log >= 600:  # 10 minutes
                    # Build active zone details with learned power for better diagnostics
                    try:
                        zone_details = []
                        for z in active_zones:
                            short = z.split(".")[-1]
                            try:
                                p = round(
                                    self.get_learned_power(short, self.season_mode)
                                )
                            except Exception:
                                p = None
                            if p is None:
                                zone_details.append(f"{z}")
                            else:
                                zone_details.append(f"{z}({p}W)")
                        zones_str = ",".join(zone_details) if zone_details else "none"
                    except Exception:
                        zones_str = ",".join(active_zones) if active_zones else "none"

                    msg = (
                        f"[SYSTEM_BALANCED] grid={round(self.ema_30s)}W "
                        f"solar={round(self.ema_30s + self.ema_5m)}W "
                        f"ema30s={round(self.ema_30s)}W ema5m={round(self.ema_5m)}W "
                        f"active_zones={on_count} zones=[{zones_str}] "
                        f"unified_conf={round(self.confidence,2)} "
                        f"(add={round(self.last_add_conf,2)},remove={round(self.last_remove_conf,2)}) "
                        f"samples={self.samples} season_mode={self.season_mode}"
                    )

                    await self._log(msg)
                    self._last_balanced_log_time = now_ts

                    # Notify listeners so diagnostic sensor state updates immediately
                    try:
                        self.async_update_listeners()
                    except Exception:
                        # Do not let listener notification errors affect main loop
                        _LOGGER.debug(
                            "Failed to async_update_listeners after balanced state",
                            exc_info=True,
                        )

                # Periodic cleanup of stale tracking data (every hour)
                now_ts = dt_util.utcnow().timestamp()
                if getattr(self, "_last_cleanup_time", 0) + 3600 < now_ts:
                    self._cleanup_stale_tracking_data()
                    self._last_cleanup_time = now_ts

                # Update adaptive interval based on system state
                self._update_adaptive_interval()

                self.metrics.record_cycle_end(cycle_start, success=True)
            except (SensorUnavailableError, SensorInvalidError) as e:
                # Sensor issues are expected during startup or temporary outages
                self.note = f"Sensor error: {e}"
                _LOGGER.warning("Sensor error in update cycle: %s", e)
                self.metrics.record_cycle_end(cycle_start, success=False)
            except (
                asyncio.CancelledError,
                OSError,
                ValueError,
                TypeError,
                AttributeError,
            ) as e:
                self.note = f"Unexpected error in update cycle: {e}"
                _LOGGER.exception("Unexpected error in _async_update_data")
                self.metrics.record_cycle_end(cycle_start, success=False)

        # -------------------------------------------------------------------------
        # EMA / metrics / guards
        # -------------------------------------------------------------------------

    def _update_ema(self, grid_raw: float) -> None:
        """Update EMA metrics for grid power."""
        old_ema_30s = self.ema_30s
        old_ema_5m = self.ema_5m

        self.ema_30s, self.ema_5m = self.ema_tracker.update(grid_raw)

        # Validate EMA values
        if not (
            isinstance(self.ema_30s, (int, float))
            and isinstance(self.ema_5m, (int, float))
        ):
            # Log EMA validation failure asynchronously on HA loop
            try:
                self.create_task(
                    self._log_ema_validation_failure(
                        "non_numeric", grid_raw, old_ema_30s, old_ema_5m
                    )
                )
            except Exception:
                try:
                    self.hass.async_create_task(
                        self._log_ema_validation_failure(
                            "non_numeric", grid_raw, old_ema_30s, old_ema_5m
                        )
                    )
                except Exception:
                    pass
            # Reset to safe values
            self.ema_tracker.reset()
            self.ema_30s, self.ema_5m = 0.0, 0.0
        elif not (-50000 <= self.ema_30s <= 50000) or not (
            -50000 <= self.ema_5m <= 50000
        ):
            try:
                self.create_task(
                    self._log_ema_validation_failure(
                        "out_of_range", grid_raw, old_ema_30s, old_ema_5m
                    )
                )
            except Exception:
                try:
                    self.hass.async_create_task(
                        self._log_ema_validation_failure(
                            "out_of_range", grid_raw, old_ema_30s, old_ema_5m
                        )
                    )
                except Exception:
                    pass
            # Reset to safe values
            self.ema_tracker.reset()
            self.ema_30s, self.ema_5m = 0.0, 0.0

    async def _log_ema_validation_failure(
        self,
        failure_type: str,
        input_value: float,
        old_ema_30s: float,
        old_ema_5m: float,
    ) -> None:
        """Log EMA validation failure."""
        if failure_type == "non_numeric":
            message = f"Power calculation error: received invalid data ({round(input_value, 2)}) - resetting calculations"
        else:  # out_of_range
            message = f"Power calculation out of range: value {round(input_value, 2)} exceeded safety limits - resetting calculations"

        await self._log(message, "warning")

    def _update_temp_ema_10m(self, zone_id: str, current_temp: float) -> None:
        """Update 10-minute EMA for temperature stability tracking."""
        if zone_id not in self.temp_ema_10m:
            self.temp_ema_10m[zone_id] = current_temp
        else:
            self.temp_ema_10m[zone_id] = (
                EMA_10M_ALPHA * current_temp
                + (1 - EMA_10M_ALPHA) * self.temp_ema_10m[zone_id]
            )

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
                        temp = float(st.state)
                        self.zone_current_temps[zone_id] = temp
                        # Update 10-minute EMA for temperature stability
                        self._update_temp_ema_10m(zone_id, temp)
                        continue
                    except (TypeError, ValueError):
                        pass

            # Fallback: try climate entity current_temperature attribute
            zone_state = self.hass.states.get(zone_id)
            if zone_state and zone_state.domain == "climate":
                current_temp = zone_state.attributes.get("current_temperature")
                if current_temp is not None:
                    try:
                        temp = float(current_temp)
                        self.zone_current_temps[zone_id] = temp
                        # Update 10-minute EMA for temperature stability
                        self._update_temp_ema_10m(zone_id, temp)
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
            # No zone specified or no season mode set; conservatively treat as not at target
            return False

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

    async def _perform_zone_swap(self, zone_to_remove: str, zone_to_add: str) -> None:
        """Perform a zone swap: remove satisfied zone, add needy zone."""
        try:
            # Prevent rapid swapping (minimum 5 minutes between swaps for same zone)
            now_ts = dt_util.utcnow().timestamp()
            last_swap = self.zone_last_swap_time.get(zone_to_remove, 0)
            if now_ts - last_swap < ZONE_SWAP_MIN_INTERVAL_SECONDS:  # 5 minutes
                return

            # Log the swap
            remove_name = zone_to_remove.split(".")[-1]
            add_name = zone_to_add.split(".")[-1]
            await self._log(
                f"Zone optimization: deactivating '{remove_name}' (comfort target reached), "
                f"activating '{add_name}' (needs cooling/heating)"
            )

            # Remove the satisfied zone
            await self.action_executor.attempt_remove_zone(zone_to_remove, self.ema_5m)

            # Add the needy zone (use current power readings)
            ac_power = self._validate_sensor_state(
                self.hass.states.get(self.config_manager.get(CONF_AC_POWER_SENSOR)),
                "AC power sensor",
            )
            required_export = self._compute_required_export(
                zone_to_add, mode=self.season_mode
            )
            if required_export is None:
                return
            await self.action_executor.attempt_add_zone(
                zone_to_add,
                ac_power,
                -self.ema_30s,  # export
                required_export,
            )

            # Record swap time
            self.zone_last_swap_time[zone_to_remove] = now_ts
            self.last_action = "zone_swap"

        except (ValueError, TypeError, AttributeError, KeyError) as e:
            _LOGGER.exception(f"Failed to perform zone swap: {e}")

    async def _perform_freeze_cleanup(self) -> None:
        """Cancel tasks and reset learning state when master is off or solar is too low."""
        # Flush any pending storage saves before cleanup
        try:
            await self._flush_pending_storage_save()
        except (asyncio.CancelledError, OSError) as exc:
            _LOGGER.debug(
                "Error flushing pending storage save during freeze cleanup: %s", exc
            )

        # Cancel panic task via PanicManager to avoid race conditions
        try:
            if getattr(self, "panic_manager", None) is not None:
                await self.panic_manager.cancel_panic()
        except (asyncio.CancelledError, AttributeError) as exc:
            _LOGGER.debug("Error while cancelling panic during freeze cleanup: %s", exc)

        # Reset controller learning state (safe)
        try:
            if getattr(self, "controller", None) is not None:
                await self.controller._reset_learning_state_async()
        except (asyncio.CancelledError, AttributeError):
            _LOGGER.debug(
                "Controller reset learning method failed or controller not set"
            )

        # Track master_off_since for EMA reset
        now_ts = dt_util.utcnow().timestamp()
        if self.master_off_since is None:
            self.master_off_since = now_ts

        # Reset EMA after long OFF
        if now_ts - self.master_off_since >= EMA_RESET_AFTER_OFF_SECONDS:
            if self.ema_30s != 0.0 or self.ema_5m != 0.0:
                await self._log("[EMA_RESET_AFTER_MASTER_OFF] resetting EMA")
            self.ema_tracker.reset()

    def _cleanup_stale_tracking_data(self) -> None:
        """Remove tracking data for zones no longer in configuration."""
        current_zones = set(self.config.get(CONF_ZONES, []))

        # Clean temp EMA tracking
        stale_zones = set(self.temp_ema_10m.keys()) - current_zones
        for zone in stale_zones:
            del self.temp_ema_10m[zone]

        # Clean swap time tracking
        stale_zones = set(self.zone_last_swap_time.keys()) - current_zones
        for zone in stale_zones:
            del self.zone_last_swap_time[zone]

        # Also clean: zone_last_changed, zone_manual_lock_until, zone_current_temps
        for tracking_dict in [
            self.zone_last_changed,
            self.zone_manual_lock_until,
            self.zone_current_temps,
            self.zone_last_changed_type,
            self.zone_last_state,
        ]:
            stale = set(tracking_dict.keys()) - current_zones
            for zone in stale:
                del tracking_dict[zone]

    def _update_adaptive_interval(self) -> None:
        """Update update interval based on system state for adaptive performance."""
        base_interval = self.config_manager.get_int(
            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        )

        # Use faster updates during active states
        if self.panic_manager and self.panic_manager.is_panicking:
            new_interval = min(base_interval, 5)  # Max 5 seconds during panic
        elif self.controller and self.controller.is_learning:
            new_interval = min(base_interval, 8)  # Max 8 seconds during learning
        elif len(getattr(self, "active_zones", [])) > 0:
            new_interval = min(base_interval, 10)  # Max 10 seconds with active zones
        else:
            new_interval = base_interval  # Use configured interval for stable state

        # Only update if interval changed
        if self.update_interval is not None:
            current_interval = self.update_interval.total_seconds()
            if new_interval != current_interval:
                self.update_interval = timedelta(seconds=new_interval)
                _LOGGER.debug(f"Adaptive update interval changed to {new_interval}s")

    async def _async_cleanup_tasks(self) -> None:
        """Clean up running tasks during shutdown."""
        # Cancel panic task
        if getattr(self, "panic_manager", None) is not None:
            await self.panic_manager.cancel_panic()

        # Cancel any pending storage save
        await self._flush_pending_storage_save()

        # Cancel any debounced storage task
        if self._storage_debounce_task and not self._storage_debounce_task.done():
            self._storage_debounce_task.cancel()
            try:
                await self._storage_debounce_task
            except asyncio.CancelledError:
                pass
