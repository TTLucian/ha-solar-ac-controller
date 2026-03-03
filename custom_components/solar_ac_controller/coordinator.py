# custom_components/solar_ac_controller/coordinator.py
from __future__ import annotations

import asyncio
import copy
import logging
from datetime import timedelta
from typing import Any, Coroutine, Dict, Literal, Optional, TypedDict, TypeVar, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .actions import ActionExecutor
from .config_manager import ConfigManager
from .const import (
    BALANCED_LOG_INTERVAL_SECONDS,
    CONF_AC_POWER_SENSOR,
    CONF_AC_SWITCH,
    CONF_ACTION_DELAY_SECONDS,
    CONF_AGGRESSIVENESS,
    CONF_COMPRESSOR_RAMP_SECONDS,
    CONF_ENABLE_TEMP_MODULATION,
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
    CONF_SOLAR_THRESHOLD_ON,
    CONF_UPDATE_INTERVAL,
    CONF_ZONES,
    DEFAULT_ACTION_DELAY_SECONDS,
    DEFAULT_AGGRESSIVENESS,
    DEFAULT_COMPRESSOR_RAMP_SECONDS,
    DEFAULT_ENABLE_TEMP_MODULATION,
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
    DEFAULT_SOLAR_THRESHOLD_ON,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    EMA_5M_ALPHA,
    EMA_10M_ALPHA,
    EMA_30S_ALPHA,
    EMA_RESET_AFTER_OFF_SECONDS,
    IDLE_POWER_EMA_ALPHA,
    IDLE_POWER_MAX_W,
    IDLE_POWER_MIN_SAMPLES,
    IDLE_POWER_SETTLE_SECONDS,
    LEARNING_TIMEOUT_SECONDS,
    LOGBOOK_THROTTLE_SECONDS,
    MAX_ZONE_HISTORY_RECORDS,
    PANIC_COOLDOWN_SECONDS,
    STALE_TRACKING_CLEANUP_INTERVAL_SECONDS,
    STRAY_ZONE_THRESHOLD_W,
    ZONE_SWAP_MIN_INTERVAL_SECONDS,
)
from .decisions import DecisionEngine
from .exceptions import SensorInvalidError, SensorUnavailableError, StorageError
from .helpers import EmaTracker, MasterSwitchController, calculate_ema
from .metrics import MetricsCollector
from .panic import PanicManager
from .zone_config_parser import ZoneConfigParser
from .zones import ZoneManager


# Type definitions for better type safety
class ZonePowerData(TypedDict, total=False):
    """Power data for a zone."""

    default: float
    heat: float
    cool: float
    lead_delta: float


# Type variables and literals for better type safety
T = TypeVar("T")
LogLevel = Literal["debug", "info", "warning", "error"]

# Type aliases for better readability
LearnedPowerData = Dict[str, ZonePowerData]
ZoneMapping = Dict[str, str]
ZoneStates = Dict[str, str]
ZoneLocks = Dict[str, Optional[float]]
SensorStates = Dict[str, Any]

_LOGGER = logging.getLogger(__name__)

# Maximum number of logbook throttling entries to prevent memory leaks
MAX_LOGBOOK_ENTRIES = 1000

# State cache TTL in seconds - should be less than typical update intervals
STATE_CACHE_TTL = 5.0


class SolarACCoordinator(DataUpdateCoordinator[SensorStates]):
    """Coordinator for Solar AC Controller integration."""

    note: str = ""  # Breadcrumb for diagnostics

    # Define all zone tracking dicts in one place for automatic cleanup
    ZONE_TRACKING_DICTS = [
        "temp_ema_10m",
        "zone_last_swap_time",
        "zone_last_changed",
        "zone_last_context_id",
        "zone_manual_lock_until",
        "zone_current_temps",
        "zone_last_changed_type",
        "zone_last_state",
        # zone_priorities is intentionally excluded: it is keyed on short names
        # (zone.split('.')[-1]) not full entity IDs, so it must be cleaned up
        # separately in _cleanup_stale_tracking_data.
        # zone_action_history is intentionally excluded: it is persisted and
        # retained for removed zones so history is not silently discarded.
    ]

    async def async_set_integration_enabled(self, enabled: bool) -> None:
        """Update and persist integration state."""
        self.integration_enabled = enabled
        await self._log(
            f"Integration {'enabled' if enabled else 'disabled'} by user.", "info"
        )
        # When disabling: cancel any running panic task immediately so nothing
        # keeps running in the background after the switch is turned off.
        if not enabled:
            if getattr(self, "panic_manager", None) is not None:
                await self.panic_manager.cancel_panic()
        async with self._storage_lock:
            self.stored_data["integration_enabled"] = enabled
            self._storage_dirty = True  # Mark as dirty

        try:
            await self._debounced_save()
        except (asyncio.CancelledError, OSError, ValueError) as exc:
            _LOGGER.exception(
                "Error scheduling integration enabled state save: %s", exc
            )
        self._debounce_recalc()

    async def async_set_activity_logging_enabled(self, enabled: bool) -> None:
        """Toggle activity logging and persist state."""
        self.activity_logging_enabled = enabled
        await self._log(
            f"Activity logging {'enabled' if enabled else 'disabled'} by user.", "info"
        )
        async with self._storage_lock:
            self.stored_data["activity_logging_enabled"] = enabled
            self._storage_dirty = True  # Mark as dirty

        try:
            await self._debounced_save()
        except (asyncio.CancelledError, OSError, ValueError) as exc:
            _LOGGER.exception("Error scheduling activity logging state save: %s", exc)
        self._debounce_recalc()

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
            config_entry=config_entry,
        )
        self.store = store
        self.stored_data = stored or {}
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
        self._state_lock = asyncio.Lock()
        self._cache_lock = asyncio.Lock()
        self._storage_dirty = False  # Track if data has actually changed

        # Recalc debouncing for service calls
        self._pending_recalc = False
        self._debounce_task: asyncio.TimerHandle | None = None

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

        # Validate configuration BEFORE initializing components (basic validation only)
        self._validate_configuration_basic()
        self._config_validated = True

        # Initialize core components
        self._init_core_components()

        # Initialize configuration values
        self._init_config_values()

        # Initialize zone mappings
        self._init_zone_mappings()

        # Validate zone temperature sensors (requires zone mappings to be initialized)
        self._validate_zone_temp_sensors()

        # Initialize runtime state
        self._init_runtime_state()

        # Flag to log configuration validation on first update
        self._config_validation_logged = False

        # Season mode (manual selection: heat or cool)

    @property
    def season_mode(self) -> str:
        # Check runtime value first, then stored data, then config
        if hasattr(self, "_season_mode"):
            return cast(str, self._season_mode)
        return cast(
            str,
            self.stored_data.get("season_mode")
            or self.config_manager.get(CONF_SEASON_MODE, DEFAULT_SEASON_MODE),
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

    @property
    def unified_add_threshold(self) -> float:
        """
        Compute dynamic Add Threshold based on Aggressiveness (a).
        Formula: 80 - (60 * a)
        Conservative (0.0): 80 | Default (0.5): 50 | Aggressive (1.0): 20
        """
        if hasattr(self, "_unified_add_threshold"):
            return self._unified_add_threshold
        a = self.aggressiveness
        return 80.0 - (60.0 * a)

    @unified_add_threshold.setter
    def unified_add_threshold(self, value: float) -> None:
        """Allow setting for testing purposes."""
        self._unified_add_threshold = value

    @property
    def unified_remove_threshold(self) -> float:
        """
        Compute dynamic Remove Threshold based on Aggressiveness (a).
        Formula: -70 + (50 * a)
        Conservative (0.0): -70 | Default (0.5): -45 | Aggressive (1.0): -20
        """
        if hasattr(self, "_unified_remove_threshold"):
            return self._unified_remove_threshold
        a = self.aggressiveness
        # Ensure a minimum deadband of 50 points to prevent chatter in noisy systems
        add = self.unified_add_threshold
        raw_remove = -70.0 + (50.0 * a)
        return min(raw_remove, add - 50.0)

    @unified_remove_threshold.setter
    def unified_remove_threshold(self, value: float) -> None:
        """Allow setting for testing purposes."""
        self._unified_remove_threshold = value

    async def async_set_season_mode(self, value: str) -> None:
        """Set season mode and persist state."""
        self.season_mode = value
        async with self._storage_lock:
            self.stored_data["season_mode"] = value
            self._storage_dirty = True  # Mark as dirty

        try:
            await self._debounced_save()
        except (asyncio.CancelledError, OSError, ValueError) as exc:
            _LOGGER.exception("Error scheduling season mode save: %s", exc)
        self._debounce_recalc()

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
            assert self.config_entry is not None
            self.hass.config_entries.async_update_entry(
                self.config_entry, options=new_options
            )
        except Exception as exc:  # pragma: no cover - defensive
            _LOGGER.exception("Failed to persist aggressiveness option: %s", exc)

        # Notify listeners so entity states refresh
        self._debounce_recalc()

    def _init_runtime_state(self) -> None:
        """Initialize runtime state variables."""
        # Zone management state
        self.next_zone: str | None = None
        self.last_zone: str | None = None
        self.active_zones: list[str] = []

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
        # Track commanded state to handle switch entity lag
        self.master_commanded_state: str | None = None
        self.master_last_command_time: float = 0.0
        # Prevent repeated EMA resets for the same off period
        self.master_ema_reset_done: bool = False

        # Controller and confidence tracking
        from .controller import SolarACController

        self.controller = SolarACController(self.hass, self, self.store)
        self.last_add_conf = 0.0
        self.last_remove_conf = 0.0
        self.confidence = 0.0
        self.last_action_start_ts: float | None = None
        self.last_action_duration: float | None = None
        self._panic_task: Optional[asyncio.Task[None]] = None
        self._panic_active = False
        self.last_panic_ts: float | None = None

        # Learning state
        self.last_action: str | None = None
        self.was_in_freeze = False  # Track previous freeze state for logging
        # Last decision state for transition logging (STABLE / ADD_READY / REMOVE_READY)
        self._last_decision_state: str | None = None
        self.learning_start_time: float | None = None
        self.ac_power_before: float | None = None
        self.learning_zone: str | None = None
        self.ema_30s = 0.0
        self.ema_5m = 0.0
        # Compressor recovery timestamp (unix ts) - prevents rapid re-add until compressor ramps
        self.compressor_recover_until = 0.0
        self.next_decision_allowed_at = 0.0  # For UI visibility of ramp lock expiry

        # Integration active state for solar-based freezing
        self.integration_active = False

        # Cached learning active flag for synchronous/lock-free reads
        self.learning_active_cached = False

        # Per-decision diagnostic breakdowns (populated by DecisionEngine)
        self.last_add_breakdown: dict = {}
        self.last_remove_breakdown: dict = {}

        # Idle compressor power learning
        self.learned_idle_power: float = 0.0  # EMA of standby draw when no zones are on
        self.idle_power_samples: int = 0  # Number of samples collected

        # Zone action history ring buffers (persisted; source=integration|manual|panic|freeze)
        self.zone_action_history: dict[str, list[dict]] = {}

        # Per-zone last-issued HA context ID for authorship-based override detection
        self.zone_last_context_id: dict[str, tuple[str, float]] = (
            {}
        )  # zone -> (ctx_id, issued_ts)

        # Temperature stability tracking for zone swapping
        self.temp_ema_10m: dict[str, float] = {}  # zone -> 10min EMA temperature
        self.zone_last_swap_time: dict[str, float] = {}  # zone -> last swap timestamp
        # Current zone temperatures (updated each cycle by _read_zone_temps)
        self.zone_current_temps: dict[str, float | None] = {}

        # Logbook throttle state – initialised here to avoid lazy-init in hot path
        self._last_logbook_emit: dict[str, float] = {}
        self._logbook_throttle_seconds = LOGBOOK_THROTTLE_SECONDS
        # Cached diagnostics entity ID (resolved once, reused for every log entry)
        self._diagnostics_entity_id_cached: str | None = None

        # Defensive initialization
        self.required_export_source = "Initializing"

        # Sensor recovery tracking
        self._sensor_unavailable_since: Dict[str, float] = (
            {}
        )  # sensor_id -> timestamp when it became unavailable

    def _debounce_recalc(self) -> None:
        """Debounce recalculation triggers from rapid service calls."""
        self._pending_recalc = True
        if self._debounce_task is None:
            self._debounce_task = self.hass.loop.call_later(1.0, self._execute_recalc)

    def _execute_recalc(self) -> None:
        """Execute debounced recalculation."""
        self._pending_recalc = False
        self._debounce_task = None
        self.async_update_listeners()

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

    def _init_core_components(self) -> None:
        """Initialize core component instances."""
        self.zone_manager = ZoneManager(self)
        self.panic_manager = PanicManager(self)
        self.decision_engine = DecisionEngine(self)
        self.action_executor = ActionExecutor(self)
        self.ema_tracker = EmaTracker()
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
        assert self.config_entry is not None
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

        self.learned_power: LearnedPowerData = {}
        self.samples = int(raw_samples)

        # Idle compressor power (persisted across restarts)
        self.learned_idle_power = float(stored.get("idle_power", 0.0) or 0.0)
        self.idle_power_samples = int(stored.get("idle_power_samples", 0) or 0)

        # Zone action history (persisted ring buffer)
        raw_history = stored.get("zone_action_history", {}) or {}
        if isinstance(raw_history, dict):
            self.zone_action_history = {
                k: list(v) for k, v in raw_history.items() if isinstance(v, list)
            }
        else:
            self.zone_action_history = {}

        # Since we're starting fresh, just ensure data matches our TypedDict
        if isinstance(raw_learned, dict):
            for zone_name, val in raw_learned.items():
                if isinstance(val, dict):
                    # Try to use the data if it looks valid
                    try:
                        normalized: dict[str, float | str] = {}
                        for k, vv in val.items():
                            if isinstance(vv, (int, float)):
                                normalized[k.lower()] = float(vv)
                            elif isinstance(vv, str):
                                # Preserve string metadata fields (e.g. "category")
                                normalized[k.lower()] = vv

                        # Ensure required fields exist
                        if "default" not in normalized:
                            normalized["default"] = float(self.initial_learned_power)
                        if "heat" not in normalized:
                            normalized["heat"] = normalized["default"]
                        if "cool" not in normalized:
                            normalized["cool"] = normalized["default"]
                        if "lead_delta" not in normalized:
                            normalized["lead_delta"] = 0.0

                        self.learned_power[zone_name] = cast(ZonePowerData, normalized)
                    except (ValueError, TypeError):
                        # Reset to defaults if data is malformed
                        base = float(self.initial_learned_power)
                        self.learned_power[zone_name] = {
                            "default": base,
                            "heat": base,
                            "cool": base,
                            "lead_delta": 0.0,
                        }
                else:
                    # Reset to defaults for any non-dict data
                    base = float(self.initial_learned_power)
                    self.learned_power[zone_name] = {
                        "default": base,
                        "heat": base,
                        "cool": base,
                        "lead_delta": 0.0,
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
        # entry is Dict[str, Any]
        val = None
        if mode and mode in entry:
            val = entry[mode]  # type: ignore[literal-required]
        elif "default" in entry:
            val = entry["default"]
        elif "heat" in entry:
            val = entry["heat"]
        elif "cool" in entry:
            val = entry["cool"]
        if val is not None and isinstance(val, (int, float)):
            return float(val)
        return float(self.initial_learned_power)

    def get_peak_delta(
        self,
        zone_name: str,
        mode: Optional[str] = None,
    ) -> float | None:
        """Return peak delta for a zone and mode, or None if not available."""
        entry = self.learned_power.get(zone_name)
        if not entry:
            return None
        target_key = mode if mode in ["heat", "cool"] else "default"
        for key in (f"peak_delta_{target_key}", "peak_delta_default"):
            val = entry.get(key)
            if val is not None:
                try:
                    return float(val)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    pass
        return None

    def get_stabilized_delta(
        self,
        zone_name: str,
        mode: Optional[str] = None,
    ) -> float | None:
        """Return stabilized delta for a zone and mode, or None if not available."""
        entry = self.learned_power.get(zone_name)
        if not entry:
            return None
        target_key = mode if mode in ["heat", "cool"] else "default"
        for key in (f"stabilized_delta_{target_key}", "stabilized_delta_default"):
            val = entry.get(key)
            if val is not None:
                try:
                    return float(val)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    pass
        return None

    def get_time_to_peak(
        self,
        zone_name: str,
        mode: Optional[str] = None,
    ) -> float | None:
        """Return time_to_peak for a zone and mode, or None if not available."""
        entry = self.learned_power.get(zone_name)
        if not entry:
            return None
        target_key = mode if mode in ["heat", "cool"] else "default"
        for key in (f"time_to_peak_{target_key}", "time_to_peak_default"):
            val = entry.get(key)
            if val is not None:
                try:
                    return float(val)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    pass
        return None

    def set_learned_power(
        self,
        zone_name: str,
        value: float,
        mode: str | None = None,
        category: str | None = None,
        time_to_peak: float | None = None,
        peak_delta: float | None = None,
        stabilized_delta: float | None = None,
    ) -> None:
        """Update learned power for a specific zone and mode, including phase data."""
        # Ensure the zone exists as a dictionary
        if zone_name not in self.learned_power:
            base = float(self.initial_learned_power)
            self.learned_power[zone_name] = {
                "default": base,
                "heat": base,
                "cool": base,
                "lead_delta": 0.0,
            }

        entry = self.learned_power[zone_name]
        target_key = mode if mode in ["heat", "cool"] else "default"
        entry[target_key] = float(value)  # type: ignore[literal-required]

        # Persist optional phase-detection data alongside the power value.
        # Keys use target_key suffix so heat/cool/default stay independent.
        if peak_delta is not None:
            entry[f"peak_delta_{target_key}"] = float(peak_delta)  # type: ignore[literal-required]
        if stabilized_delta is not None:
            entry[f"stabilized_delta_{target_key}"] = float(stabilized_delta)  # type: ignore[literal-required]
        if time_to_peak is not None:
            entry[f"time_to_peak_{target_key}"] = float(time_to_peak)  # type: ignore[literal-required]
        if category is not None:
            entry["category"] = category  # type: ignore[typeddict-unknown-key]

        self._storage_dirty = True
        _LOGGER.debug(
            "Learned new power for %s (%s): %sW peak_delta=%s stabilized_delta=%s",
            zone_name,
            target_key,
            value,
            peak_delta,
            stabilized_delta,
        )

    def _record_zone_action(
        self,
        zone: str,
        action: str,
        source: str,
        reason: str = "",
        confidence: float | None = None,
        export_margin: float | None = None,
    ) -> None:
        """Append a timestamped record to the per-zone action history ring buffer.

        ``source`` should be one of: ``"integration"``, ``"manual"``, ``"panic"``,
        ``"freeze"``.  ``action`` is an arbitrary short label such as ``"on"``,
        ``"off"``, ``"manual_on"``, etc.
        """
        now = dt_util.utcnow()
        record: dict = {
            "ts": now.timestamp(),
            "ts_iso": now.isoformat(),
            "action": action,
            "source": source,
        }
        if reason:
            record["reason"] = reason
        if confidence is not None:
            record["confidence"] = round(confidence, 3)
        if export_margin is not None:
            record["export_margin"] = round(export_margin, 1)

        history = self.zone_action_history.setdefault(zone, [])
        history.append(record)
        # Trim to ring-buffer size
        if len(history) > MAX_ZONE_HISTORY_RECORDS:
            del history[: len(history) - MAX_ZONE_HISTORY_RECORDS]
        self._storage_dirty = True

    async def async_persist_learned_values(self) -> None:
        """Persist learned values to storage."""
        try:
            # Update stored_data under lock so readers/savers don't race
            async with self._storage_lock:
                self.stored_data["learned_power"] = self._rounded_power(
                    self.learned_power
                )
                self.stored_data["samples"] = int(self.samples)
                self.stored_data["idle_power"] = round(self.learned_idle_power, 1)
                self.stored_data["idle_power_samples"] = int(self.idle_power_samples)
                self.stored_data["zone_action_history"] = dict(self.zone_action_history)
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
    def _throttle_logbook_key(self, message: str, level: str) -> str:
        """Create stable key for logbook throttling by extracting message pattern.

        For DEBUG-level messages that start with a bracketed tag (e.g. ``[ZONE_CALC]``,
        ``[CONFIDENCE]``, ``[SENSORS]``), the key is ``level:[TAG]``.  This prevents
        per-cycle messages whose numeric values change every update from generating a
        unique key each time, which would effectively bypass the throttle and spam the
        logbook when activity logging is enabled.

        For INFO/WARNING/ERROR messages the full first-three-words heuristic is kept so
        that distinct info messages with the same tag (e.g. ``[ADD_ZONE]`` for different
        zones) are *not* collapsed into a single throttle bucket.
        """
        if level == "DEBUG" and message.startswith("["):
            tag_end = message.find("]")
            if tag_end > 0:
                return f"{level}:{message[: tag_end + 1]}"
        # Fallback: first 3 words create a stable pattern for non-periodic messages
        words = message.split()[:3] if message else [""]
        pattern = " ".join(words).strip()[:50]
        return f"{level}:{pattern}"

    async def _log(self, message: str, level: LogLevel | None = "info") -> None:
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

                    # Resolve the diagnostics entity id once and cache it.
                    # The entity registry is queried only on the first log call;
                    # subsequent calls reuse the cached value.
                    # Use getattr() to guard against cases where __init__ was not
                    # called (e.g. object.__new__() in tests or future migrations).
                    if getattr(self, "_diagnostics_entity_id_cached", None) is None:
                        assert self.config_entry is not None
                        try:
                            from homeassistant.helpers import entity_registry as er

                            registry = er.async_get(self.hass)
                            unique_id = f"{self.config_entry.entry_id}_diagnostics"
                            reg_entry = registry.async_get_entity_id(
                                "sensor", DOMAIN, unique_id
                            )
                            self._diagnostics_entity_id_cached = (
                                reg_entry
                                or f"sensor.{self.config_entry.entry_id}_diagnostics"
                            )
                        except Exception:
                            self._diagnostics_entity_id_cached = (
                                f"sensor.{self.config_entry.entry_id}_diagnostics"
                            )
                    diagnostics_entity_id = self._diagnostics_entity_id_cached

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

                    # Throttle repeated messages to avoid logbook spam using stable keys
                    now_ts = dt_util.utcnow().timestamp()
                    key = self._throttle_logbook_key(message, logbook_level)
                    last = self._last_logbook_emit.get(key, 0.0)
                    if now_ts - last >= float(self._logbook_throttle_seconds):
                        # Clean up old entries if dictionary is getting too large
                        if len(self._last_logbook_emit) > MAX_LOGBOOK_ENTRIES:
                            # Remove oldest 20% of entries
                            sorted_entries = sorted(
                                self._last_logbook_emit.items(), key=lambda x: x[1]
                            )
                            to_remove = sorted_entries[: MAX_LOGBOOK_ENTRIES // 5]
                            for old_key, _ in to_remove:
                                del self._last_logbook_emit[old_key]

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
        try:
            async with asyncio.timeout(2):  # 2-second timeout for storage operations
                async with self._storage_lock:
                    # Check dirty flag INSIDE the lock to prevent TOCTOU race
                    if not self._storage_dirty:
                        return

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
                        # Use create_background_task so the debounce sleep does not
                        # register with HA's bootstrap tracker and delay startup.
                        self._storage_debounce_task = self.create_background_task(
                            self._delayed_save(delay)
                        )
                        return

                    # Save immediately if enough time has passed
                    await self._perform_storage_save()
                    self._last_storage_save = now
        except asyncio.TimeoutError:
            _LOGGER.error("Storage lock acquisition timed out - possible deadlock!")

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
                self._storage_debounce_task = None
        # Always flush if dirty, regardless of whether a task was pending
        if self._storage_dirty:
            async with self._storage_lock:
                await self._perform_storage_save()
                self._last_storage_save = dt_util.utcnow().timestamp()

    async def _perform_storage_save(self) -> None:
        """Perform the actual storage save operation."""
        try:
            # Copy stored_data to avoid races where other coroutines mutate
            # the in-memory dict while the I/O operation is in progress.
            # Deep copy nested structures but shallow copy primitives for performance.
            data_to_save = {}
            for key, value in self.stored_data.items():
                if isinstance(value, (dict, list)):
                    data_to_save[key] = copy.deepcopy(value)
                else:
                    data_to_save[key] = value
            await self.store.async_save(data_to_save)
            self._storage_dirty = False  # Clear dirty flag on successful save
        except (OSError, StorageError) as exc:
            _LOGGER.exception("Error saving to storage: %s", exc)

    async def _get_cached_state(self, entity_id: str) -> Any:
        """Get entity state with caching for performance."""
        async with self._cache_lock:
            now = dt_util.utcnow().timestamp()

            # Invalidate cache after TTL expires to balance performance vs freshness
            if now - self._cache_timestamp > STATE_CACHE_TTL:
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
            self.create_background_task(
                self._log_sensor_recovery(sensor_name, unavailable_duration)
            )
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

    def _cleanup_sensor_tracking(self) -> None:
        """Remove stale sensor tracking entries."""
        configured_sensors = {
            self.config.get(CONF_GRID_SENSOR),
            self.config.get(CONF_SOLAR_SENSOR),
            self.config.get(CONF_AC_POWER_SENSOR),
        }
        # Remove None values that might be in the set
        configured_sensors.discard(None)

        stale_sensors = set(self._sensor_unavailable_since.keys()) - configured_sensors
        for sensor in stale_sensors:
            del self._sensor_unavailable_since[sensor]

    def create_task(self, coro: Coroutine[None, None, T]) -> asyncio.Task[T]:
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
        return cast(asyncio.Task[Any], task)

    def create_background_task(
        self, coro: Coroutine[Any, Any, Any]
    ) -> Optional[asyncio.Task[Any]]:
        """Create a fire-and-forget task that does NOT block HA bootstrap/shutdown.

        Uses the raw asyncio event loop (not hass.async_create_task) so that HA's
        internal task-tracker does not include this task in its bootstrap-phase
        waiting set.  Long-running tasks such as the panic delay runner would
        otherwise cause the ``Setup timed out for bootstrap`` warning.
        """
        try:
            loop = getattr(self.hass, "loop", None) or asyncio.get_event_loop()
            task: asyncio.Task[Any] = loop.create_task(coro)

            def _done_callback(t: asyncio.Task) -> None:
                try:
                    exc = t.exception()
                    if exc:
                        _LOGGER.exception("Background task exception: %s", exc)
                except asyncio.CancelledError:
                    pass

            task.add_done_callback(_done_callback)
            return task
        except Exception as e:
            _LOGGER.warning("Failed to create background task: %s", e)
            return None

    def _validate_configuration_basic(self) -> None:
        """Validate basic configuration requirements on startup."""
        from .exceptions import ConfigurationError

        required_sensors = [CONF_GRID_SENSOR, CONF_SOLAR_SENSOR, CONF_AC_POWER_SENSOR]

        for sensor in required_sensors:
            if not self.config_manager.get(sensor):
                raise ConfigurationError(f"Missing required sensor: {sensor}")

        zones = self.config_manager.get_list(CONF_ZONES, [])
        if not zones:
            raise ConfigurationError("At least one zone must be configured")

    def _validate_zone_temp_sensors(self) -> None:
        """Validate zone temperature sensors exist if configured."""
        # Validate zone temperature sensors exist if configured
        for zone, sensor in self.zone_temp_sensors.items():
            if sensor and not self.hass.states.get(sensor):
                _LOGGER.warning(f"Zone {zone} temperature sensor {sensor} not found")

    # -------------------------------------------------------------------------
    # Main update loop
    # -------------------------------------------------------------------------

    async def _async_update_data(self) -> None:  # type: ignore[override]
        """Main loop executed every 5 seconds."""
        try:
            async with asyncio.timeout(5):  # 5-second timeout to prevent deadlocks
                async with self._update_lock:
                    cycle_start = self.metrics.record_cycle_start()

                    # Log configuration validation on first run
                    if not self._config_validation_logged:
                        await self._log_configuration_validation()
                        self._config_validation_logged = True

                    try:
                        # Integration enable/disable logic
                        # When disabled we still read the solar sensor so we can
                        # auto-re-enable once solar reaches SOLAR_THRESHOLD_ON.
                        if (
                            hasattr(self, "integration_enabled")
                            and not self.integration_enabled
                        ):
                            try:
                                _solar_check = self._validate_sensor_state(
                                    await self._get_cached_state(
                                        self.config_manager.get(CONF_SOLAR_SENSOR)
                                    ),
                                    "Solar sensor",
                                )
                                _on_thr = self.config_manager.get_float(
                                    CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON
                                )
                                if _solar_check >= _on_thr:
                                    await self._log(
                                        f"[AUTO_ENABLE] solar={round(_solar_check)}W >= "
                                        f"threshold_on={_on_thr}W, re-enabling integration",
                                        "info",
                                    )
                                    self.integration_enabled = True
                                    async with self._storage_lock:
                                        self.stored_data["integration_enabled"] = True
                                        self._storage_dirty = True
                                    self._debounce_recalc()
                                    # Fall through to run the normal cycle
                                else:
                                    async with self._state_lock:
                                        self.last_action = "integration_disabled"
                                    self.note = "Integration disabled by user."
                                    _LOGGER.debug(
                                        "Integration disabled, skipping all logic."
                                    )
                                    # Even while disabled, still run master switch safety
                                    # control so the physical relay is turned off once the
                                    # compressor winds down after any previous freeze.
                                    try:
                                        _ac_pw_dis: float | None = None
                                        try:
                                            _ac_pw_dis = self._validate_sensor_state(
                                                await self._get_cached_state(
                                                    self.config_manager.get(
                                                        CONF_AC_POWER_SENSOR
                                                    )
                                                ),
                                                "AC power sensor",
                                            )
                                        except (
                                            SensorUnavailableError,
                                            SensorInvalidError,
                                        ):
                                            pass
                                        await self.master_controller.handle_master_switch(
                                            _solar_check,
                                            cycle_start,
                                            ac_power=_ac_pw_dis,
                                        )
                                    except Exception:  # noqa: BLE001
                                        pass
                                    self.metrics.record_cycle_end(
                                        cycle_start, success=True
                                    )
                                    return
                            except (SensorUnavailableError, SensorInvalidError):
                                # Solar unreadable while disabled – stay disabled
                                async with self._state_lock:
                                    self.last_action = "integration_disabled"
                                self.metrics.record_cycle_end(cycle_start, success=True)
                                return

                        # 1. Read sensors (grid, solar, ac_power)
                        grid_raw = self._validate_sensor_state(
                            await self._get_cached_state(
                                self.config_manager.get(CONF_GRID_SENSOR)
                            ),
                            "Grid sensor",
                        )
                        solar = self._validate_sensor_state(
                            await self._get_cached_state(
                                self.config_manager.get(CONF_SOLAR_SENSOR)
                            ),
                            "Solar sensor",
                        )

                        # Solar-based integration freeze/unfreeze logic
                        on_threshold = self.config_manager.get_float(
                            CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON
                        )
                        off_threshold = self.config_manager.get_float(
                            CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF
                        )

                        if not self.integration_active:
                            if solar >= on_threshold:
                                # Unfreeze: solar has reached on_threshold
                                self.integration_active = True
                                object.__setattr__(
                                    self,
                                    "update_interval",
                                    timedelta(
                                        seconds=self.config_manager.get_int(
                                            CONF_UPDATE_INTERVAL,
                                            DEFAULT_UPDATE_INTERVAL,
                                        )
                                    ),
                                )
                                await self._log(
                                    f"[INTEGRATION_UNFROZEN] solar={round(solar)}W >= on_threshold={on_threshold}W, starting calculations"
                                )
                            else:
                                # Still frozen: check less frequently
                                object.__setattr__(
                                    self, "update_interval", timedelta(seconds=300)
                                )  # check every 5 min
                                async with self._state_lock:
                                    self.last_action = "integration_frozen"
                                self.note = f"Integration frozen: solar {round(solar)}W < on_threshold {on_threshold}W"
                                # Even while frozen, still run master switch safety
                                # control so the physical relay is turned off once the
                                # compressor winds down after zones are off.
                                try:
                                    _ac_pw_frz: float | None = None
                                    try:
                                        _ac_pw_frz = self._validate_sensor_state(
                                            await self._get_cached_state(
                                                self.config_manager.get(
                                                    CONF_AC_POWER_SENSOR
                                                )
                                            ),
                                            "AC power sensor",
                                        )
                                    except (SensorUnavailableError, SensorInvalidError):
                                        pass
                                    await self.master_controller.handle_master_switch(
                                        solar, cycle_start, ac_power=_ac_pw_frz
                                    )
                                except Exception:  # noqa: BLE001
                                    pass
                                self.metrics.record_cycle_end(cycle_start, success=True)
                                return
                        else:
                            if solar <= off_threshold:
                                # Freeze: solar has dropped to or below off_threshold
                                await self._log(
                                    f"[INTEGRATION_FREEZING] solar={round(solar)}W <= off_threshold={off_threshold}W, turning off zones – master will cut once compressor winds down"
                                )
                                await self._perform_freeze_cleanup()
                                self.integration_active = False
                                # Also turn off the integration_enabled switch so the
                                # user can see the integration is inactive, and so it
                                # can be auto-re-enabled when solar rises again.
                                if self.integration_enabled:
                                    self.integration_enabled = False
                                    async with self._storage_lock:
                                        self.stored_data["integration_enabled"] = False
                                        self._storage_dirty = True
                                    self._debounce_recalc()
                                object.__setattr__(
                                    self, "update_interval", timedelta(seconds=300)
                                )  # check every 5 min
                                async with self._state_lock:
                                    self.last_action = "integration_frozen"
                                self.note = f"Integration frozen: solar {round(solar)}W <= off_threshold {off_threshold}W"
                                self.metrics.record_cycle_end(cycle_start, success=True)
                                return

                        ac_power = self._validate_sensor_state(
                            await self._get_cached_state(
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

                        # Periodic cleanup of stale sensor tracking (every ~10 minutes)
                        if self._cycle_counter % 100 == 0:
                            self._cleanup_sensor_tracking()

                        # 2. EMA updates
                        self._update_ema(grid_raw)

                        # 3. Master switch auto-control (based ONLY on solar production)
                        await self.master_controller.handle_master_switch(
                            solar, cycle_start, ac_power=ac_power
                        )

                        # 4. Update zone temperatures for comfort target checking
                        self._read_zone_temps()

                        # 5. Determine zones and detect manual overrides
                        if (
                            not hasattr(self, "zone_manager")
                            or self.zone_manager is None
                        ):
                            _LOGGER.error(
                                "zone_manager is not initialized! Skipping update cycle."
                            )
                            async with self._state_lock:
                                self.last_action = "zone_manager_uninitialized"
                            return

                        active_zones = (
                            await self.zone_manager.update_zone_states_and_overrides()
                        )
                        on_count = len(active_zones)
                        self.on_count = on_count  # Set for panic manager

                        # Store active zones for decision engine
                        self.active_zones = active_zones

                        # 5.5 Idle power learning and stray zone detection
                        # Runs only when no zones are active so we measure pure standby draw.
                        if not active_zones:
                            _ac_switch_id = self.config_manager.get(CONF_AC_SWITCH)
                            _switch_on = False
                            if _ac_switch_id:
                                _sw = self.hass.states.get(_ac_switch_id)
                                _switch_on = _sw is not None and _sw.state == "on"
                            if _switch_on and ac_power is not None and ac_power > 0:
                                _now_idle = dt_util.utcnow().timestamp()
                                _last_zone_ts = (
                                    max(self.zone_last_changed.values())
                                    if self.zone_last_changed
                                    else 0.0
                                )
                                _settled = (
                                    _now_idle - _last_zone_ts
                                ) >= IDLE_POWER_SETTLE_SECONDS
                                if _settled and ac_power <= IDLE_POWER_MAX_W:
                                    # Update the idle-power EMA
                                    if self.idle_power_samples == 0:
                                        self.learned_idle_power = float(ac_power)
                                    else:
                                        self.learned_idle_power = calculate_ema(
                                            self.learned_idle_power,
                                            float(ac_power),
                                            IDLE_POWER_EMA_ALPHA,
                                        )
                                    self.idle_power_samples += 1
                                    if self.idle_power_samples % 30 == 0:
                                        await self._log(
                                            f"[IDLE_POWER] Updated baseline: "
                                            f"{round(self.learned_idle_power, 1)}W "
                                            f"(n={self.idle_power_samples})",
                                            "debug",
                                        )
                                # Stray zone detection: ac_power well above idle with no active zones.
                                # Only warn once the baseline is trusted (enough samples).
                                if (
                                    self.idle_power_samples >= IDLE_POWER_MIN_SAMPLES
                                    and ac_power
                                    > self.learned_idle_power + STRAY_ZONE_THRESHOLD_W
                                ):
                                    await self._log(
                                        f"[STRAY_ZONE] No zones active but "
                                        f"ac_power={round(ac_power)}W is "
                                        f"{round(ac_power - self.learned_idle_power)}W "
                                        f"above idle baseline "
                                        f"({round(self.learned_idle_power, 1)}W). "
                                        f"A zone may have failed to turn off.",
                                        "warning",
                                    )

                        # 6. Compute required export and confidences
                        next_zone, last_zone = (
                            await self.zone_manager.select_next_and_last_zone(
                                active_zones
                            )
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
                            None
                            if required_export is None
                            else export - required_export
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
                        self.last_remove_conf = (
                            self.decision_engine.compute_remove_conf(
                                import_power=import_power,
                                last_zone=last_zone,
                            )
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

                        # Log transitions between decision states at info level so they
                        # appear in the activity logbook without requiring debug filtering.
                        # The very first cycle (None → any) is suppressed to avoid noise
                        # on startup.
                        prev_decision_state = self._last_decision_state
                        if decision_state != prev_decision_state:
                            self._last_decision_state = decision_state
                            if prev_decision_state is not None:
                                transition_detail = (
                                    f"conf={round(self.confidence, 2)} "
                                    f"add_threshold={round(self.unified_add_threshold, 2)} "
                                    f"remove_threshold={round(self.unified_remove_threshold, 2)}"
                                )
                                if next_zone:
                                    transition_detail += (
                                        f" next={next_zone.split('.')[-1]}"
                                    )
                                if last_zone:
                                    transition_detail += (
                                        f" last={last_zone.split('.')[-1]}"
                                    )
                                await self._log(
                                    f"[STATE_CHANGE] {prev_decision_state} → {decision_state} "
                                    f"{transition_detail}",
                                    "info",
                                )

                        # Prevent decision overrides during active panic
                        if getattr(self, "_panic_active", False):
                            return

                        now_ts = dt_util.utcnow().timestamp()

                        # 7. Learning timeout
                        learning_zone = await self.controller.session.get_zone()
                        learning_start_time = (
                            await self.controller.session.get_start_time()
                        )
                        # Update cached learning flag for synchronous checks in DecisionEngine
                        try:
                            self.learning_active_cached = bool(learning_zone)
                        except Exception:
                            self.learning_active_cached = False

                        # Feed current ac_power into the learning session every cycle
                        # so phase detection (peak tracking, stabilization) works correctly.
                        if learning_zone:
                            await self.controller.session.add_power_reading(ac_power)

                        if (
                            learning_zone
                            and learning_start_time
                            and now_ts - learning_start_time >= LEARNING_TIMEOUT_SECONDS
                        ):
                            await self._log(
                                f"[LEARNING_TIMEOUT] zone={learning_zone}", "info"
                            )
                            result = await self.controller.finish_learning()
                            if not result.success:
                                await self._log(
                                    f"Learning failed: {result.error_message}",
                                    "warning",
                                )
                            return

                        # 8. Panic logic
                        if self.panic_manager.should_panic:
                            self.note = (
                                "Panic triggered: grid import exceeded threshold."
                            )
                            await self.panic_manager.schedule_panic(active_zones)
                            return

                        # 9. Panic cooldown
                        if self.panic_manager.is_in_cooldown:
                            async with self._state_lock:
                                self.last_action = "panic_cooldown"
                            # Calculate remaining cooldown time
                            now_ts = dt_util.utcnow().timestamp()
                            cooldown_remaining = max(
                                0,
                                PANIC_COOLDOWN_SECONDS
                                - (now_ts - (self.last_panic_ts or 0)),
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

                        # 10. ADD zone decision
                        if next_zone and await self.decision_engine.should_add_zone(
                            next_zone,
                            required_export if required_export is not None else 0.0,
                        ):
                            # Single zone addition (normal case)

                            # Single zone addition (normal case)
                            zone_name = next_zone.split(".")[-1]
                            learned_power = self.get_learned_power(
                                zone_name, self.season_mode
                            )
                            reason = f"Activating zone '{zone_name}' - "
                            reason += f"confidence score {round(self.confidence, 1)} meets activation threshold, "
                            reason += f"excess solar power {round(export)}W available, "
                            reason += f"zone requires {round(learned_power)}W, "
                            reason += f"currently {len(active_zones)} zones active"
                            self.note = f"Adding zone {next_zone}: confidence {round(self.confidence, 2)} >= {round(self.unified_add_threshold, 2)}"
                            await self._log(f"[ADD_ZONE] {reason}", "info")
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
                            learned_power = self.get_learned_power(
                                zone_name, self.season_mode
                            )
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

                        # 12. ZONE SWAP decision (only when no net add/remove needed)
                        # Sort active zones by reverse priority (remove lowest priority satisfied zones first)
                        for active_zone in sorted(
                            active_zones,
                            key=lambda z: self.zone_priorities.get(
                                z.split(".")[-1], 999
                            ),
                            reverse=True,
                        ):
                            zone_to_add = await self.decision_engine.should_swap_zone(
                                active_zone, import_power
                            )
                            if zone_to_add:
                                await self._perform_zone_swap(active_zone, zone_to_add)
                                return

                        # 13. SYSTEM BALANCED
                        async with self._state_lock:
                            self.last_action = "balanced"
                        self.note = f"No action: system balanced. ema30={round(self.ema_30s)}, ema5m={round(self.ema_5m)}, zones={on_count}, samples={self.samples}"

                        # Log balanced state every 10 minutes (600 seconds) to avoid spam
                        now_ts = dt_util.utcnow().timestamp()
                        last_balanced_log = getattr(self, "_last_balanced_log_time", 0)
                        if (
                            now_ts - last_balanced_log >= BALANCED_LOG_INTERVAL_SECONDS
                        ):  # 10 minutes
                            # Build active zone details with learned power for better diagnostics
                            try:
                                zone_details = []
                                for z in active_zones:
                                    short = z.split(".")[-1]
                                    try:
                                        p = round(
                                            self.get_learned_power(
                                                short, self.season_mode
                                            )
                                        )
                                    except Exception:
                                        p = None
                                    if p is None:
                                        zone_details.append(f"{z}")
                                    else:
                                        zone_details.append(f"{z}({p}W)")
                                zones_str = (
                                    ",".join(zone_details) if zone_details else "none"
                                )
                            except Exception:
                                zones_str = (
                                    ",".join(active_zones) if active_zones else "none"
                                )

                            msg = (
                                f"[SYSTEM_BALANCED] grid={round(self.ema_30s)}W "
                                f"solar={round(solar)}W "
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
                        if (
                            getattr(self, "_last_cleanup_time", 0)
                            + STALE_TRACKING_CLEANUP_INTERVAL_SECONDS
                            < now_ts
                        ):
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
                        _LOGGER.exception(
                            "Unexpected error in _async_update_data: %s", e
                        )
                        self.metrics.record_cycle_end(cycle_start, success=False)
        except asyncio.TimeoutError:
            self.note = "Update lock acquisition timed out - possible deadlock!"
            _LOGGER.error("Update lock acquisition timed out - possible deadlock!")
            # Don't record cycle metrics since we never started

        # -------------------------------------------------------------------------
        # EMA / metrics / guards
        # -------------------------------------------------------------------------

    def _update_ema(self, grid_raw: float) -> None:
        """Update EMA metrics for grid power."""
        old_ema_30s = self.ema_30s
        old_ema_5m = self.ema_5m

        self.ema_30s, self.ema_5m = self.ema_tracker.update(
            grid_raw, EMA_30S_ALPHA, EMA_5M_ALPHA
        )

        # Validate EMA values are within reasonable range
        if not (-50000 <= self.ema_30s <= 50000) or not (
            -50000 <= self.ema_5m <= 50000
        ):
            self.create_background_task(
                self._log_ema_validation_failure(
                    "out_of_range", grid_raw, old_ema_30s, old_ema_5m
                )
            )
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
        from .helpers import calculate_ema

        if zone_id not in self.temp_ema_10m:
            self.temp_ema_10m[zone_id] = current_temp
        else:
            self.temp_ema_10m[zone_id] = calculate_ema(
                self.temp_ema_10m[zone_id], current_temp, EMA_10M_ALPHA
            )

    def _compute_required_export(
        self, next_zone: str | None, mode: str | None = None
    ) -> float | None:
        """Compute required export for the next zone.

        Priority:
        1. Manual power override (if configured for zone)
        2. Mode-aware peak delta (surge cost) for decision making
        """
        if not next_zone:
            return None

        # Check for manual power override first
        if next_zone in self.zone_manual_power:
            return self.zone_manual_power[next_zone]

        zone_name = next_zone.split(".")[-1]
        peak_delta = self.get_peak_delta(zone_name, mode=mode or "default")
        if peak_delta is not None:
            return float(peak_delta)
        # Fallback to learned power if peak_delta not available
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

    async def _perform_zone_swap(self, zone_to_remove: str, zone_to_add: str) -> None:
        """Perform a zone swap: remove satisfied zone, add needy zone."""
        try:
            # Prevent rapid swapping (minimum 5 minutes between swaps for same zone)
            now_ts = dt_util.utcnow().timestamp()
            last_swap = self.zone_last_swap_time.get(zone_to_remove, 0)
            if now_ts - last_swap < ZONE_SWAP_MIN_INTERVAL_SECONDS:  # 5 minutes
                return

            # Read sensor and compute required_export BEFORE removing the zone.
            # If either step fails we must abort — removing a zone without adding
            # the replacement would lose an active zone.
            try:
                ac_power = self._validate_sensor_state(
                    self.hass.states.get(self.config_manager.get(CONF_AC_POWER_SENSOR)),
                    "AC power sensor",
                )
            except (SensorUnavailableError, SensorInvalidError) as e:
                _LOGGER.warning(
                    "Zone swap aborted — AC power sensor unavailable: %s", e
                )
                return

            required_export = self._compute_required_export(
                zone_to_add, mode=self.season_mode
            )
            if required_export is None:
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

            # Add the needy zone using the readings captured before removal
            await self.action_executor.attempt_add_zone(
                zone_to_add,
                ac_power,
                -self.ema_30s,  # export
                required_export,
            )

            # Record swap time
            self.zone_last_swap_time[zone_to_remove] = now_ts
            async with self._state_lock:
                self.last_action = "zone_swap"

        except (
            SensorUnavailableError,
            SensorInvalidError,
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
        ) as e:
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

        # Turn off all active zones – this is the primary safety action of a freeze
        zones_to_turn_off = list(self.active_zones or [])
        if zones_to_turn_off:
            await self._log(
                f"[FREEZE_CLEANUP] Turning off {len(zones_to_turn_off)} active zone(s): "
                f"{', '.join(z.split('.')[-1] for z in zones_to_turn_off)}"
            )
            for zone in zones_to_turn_off:
                try:
                    if getattr(self, "action_executor", None) is not None:
                        await self.action_executor.remove_zone(zone)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning(
                        "Failed to turn off zone '%s' during freeze cleanup: %s",
                        zone,
                        exc,
                    )

        # Reset controller learning state (safe)
        try:
            if getattr(self, "controller", None) is not None:
                await self.controller._reset_learning_state_async()
        except (asyncio.CancelledError, AttributeError):
            _LOGGER.debug(
                "Controller reset learning method failed or controller not set"
            )

        # Track master_off_since for EMA reset
        async with self._state_lock:
            now_ts = dt_util.utcnow().timestamp()
            if self.master_off_since is None:
                self.master_off_since = now_ts

            # Reset EMA after long OFF (only once per off period)
            if (
                now_ts - self.master_off_since >= EMA_RESET_AFTER_OFF_SECONDS
                and not self.master_ema_reset_done
            ):
                if self.ema_30s != 0.0 or self.ema_5m != 0.0:
                    await self._log("[EMA_RESET_AFTER_MASTER_OFF] resetting EMA")
                self.ema_tracker.reset()
                self.master_ema_reset_done = True

    def _cleanup_stale_tracking_data(self) -> None:
        """Remove tracking data for zones no longer in configuration."""
        current_zones = set(self.config.get(CONF_ZONES, []))

        # All dicts in ZONE_TRACKING_DICTS are keyed on full entity IDs.
        for dict_name in self.ZONE_TRACKING_DICTS:
            tracking_dict = getattr(self, dict_name, {})
            stale = set(tracking_dict.keys()) - current_zones
            for zone in stale:
                tracking_dict.pop(zone, None)

        # zone_priorities is keyed on short names (zone.split('.')[-1]).  Rebuild
        # it entirely from the current config so that additions, removals, and
        # reorderings are always reflected without needing a restart.
        zones_list = self.config.get(CONF_ZONES, [])
        self.zone_priorities = {
            zone.split(".")[-1]: i for i, zone in enumerate(zones_list)
        }

    def _update_adaptive_interval(self) -> None:
        """Update update interval based on system state for adaptive performance."""
        # Don't adjust interval if integration is frozen (handled by solar logic)
        if not self.integration_active:
            return

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
        current_interval = self.update_interval
        if current_interval is not None:
            current_seconds = current_interval.total_seconds()
            if new_interval != current_seconds:
                object.__setattr__(
                    self, "update_interval", timedelta(seconds=new_interval)
                )
                _LOGGER.debug(f"Adaptive update interval changed to {new_interval}s")

    async def _async_cleanup_tasks(self) -> None:
        """Clean up running tasks during shutdown."""
        # Cancel the recalc debounce call_later handle before shutting down the
        # coordinator – otherwise it fires after teardown and calls
        # async_update_listeners on a dead coordinator.
        if self._debounce_task is not None:
            self._debounce_task.cancel()
            self._debounce_task = None

        # Cancel the coordinator's refresh task
        await self.async_shutdown()

        # Cancel panic task
        if getattr(self, "panic_manager", None) is not None:
            await self.panic_manager.cancel_panic()

        # Flush any pending / dirty storage save (handles both debounced tasks and
        # unflushed dirty state that has no scheduled task yet).
        await self._flush_pending_storage_save()
