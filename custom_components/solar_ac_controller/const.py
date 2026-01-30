from __future__ import annotations

from typing import Any

"""Constants for the Solar AC Controller integration."""

# When changing the on-disk schema, increment STORAGE_VERSION and add a migration.
# Bump STORAGE_VERSION whenever the structure of the stored payload changes
# (for example: renaming keys, changing types, or moving from numeric to dict shapes).
# Keep a short note in the migration function describing the change and the target version.
# If you want the on-disk filename to remain stable across domain renames, keep STORAGE_KEY
# as a literal string (below) rather than deriving it from DOMAIN.

DOMAIN = "solar_ac_controller"

# Core configuration keys
CONF_SOLAR_SENSOR = "solar_sensor"
CONF_GRID_SENSOR = "grid_sensor"
CONF_AC_POWER_SENSOR = "ac_power_sensor"
CONF_AC_SWITCH = "ac_switch"
CONF_ZONES = "zones"
CONF_SEASON_MODE = "season_mode"  # Manual: 'heat' or 'cool'


# Solar thresholds (W)
CONF_SOLAR_THRESHOLD_ON = "solar_threshold_on"
CONF_SOLAR_THRESHOLD_OFF = "solar_threshold_off"

# Feature toggles
CONF_ENABLE_TEMP_MODULATION = "enable_temperature_modulation"
CONF_ENABLE_DIAGNOSTICS_SENSOR = "enable_diagnostics_sensor"

# Behavioral tuning
CONF_PANIC_THRESHOLD = "panic_threshold"
CONF_PANIC_DELAY = "panic_delay"
CONF_MANUAL_LOCK_SECONDS = "manual_lock_seconds"
CONF_SHORT_CYCLE_ON_SECONDS = "short_cycle_on_seconds"
CONF_SHORT_CYCLE_OFF_SECONDS = "short_cycle_off_seconds"
CONF_ACTION_DELAY_SECONDS = "action_delay_seconds"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_HIGH_FREQUENCY_MODE = "high_frequency_mode"
CONF_ACTIVITY_LOGGING = "activity_logging"

# Unified confidence thresholds (points) - hysteresis system
CONF_UNIFIED_ADD_THRESHOLD = "unified_add_threshold"
CONF_UNIFIED_REMOVE_THRESHOLD = "unified_remove_threshold"

# Learning system
CONF_INITIAL_LEARNED_POWER = "initial_learned_power"

# Comfort/zone temperature targets
CONF_MAX_TEMP_WINTER = "max_temp_winter"
CONF_MIN_TEMP_SUMMER = "min_temp_summer"
CONF_ZONE_TEMP_SENSORS = "zone_temp_sensors"
CONF_ZONE_MANUAL_POWER = "zone_manual_power"

# Enable diagnostics sensor toggle (kept original name for backward compatibility)
CONF_ENABLE_DIAGNOSTICS = "enable_diagnostics_sensor"
# Clearer alias for the diagnostics sensor toggle
CONF_ENABLE_DIAGNOSTICS_SENSOR = CONF_ENABLE_DIAGNOSTICS

# Default initial learned power used when no value is configured
DEFAULT_INITIAL_LEARNED_POWER = 1000.0

# Sensible defaults for thresholds and timing (used by coordinator if config missing)
DEFAULT_SOLAR_THRESHOLD_ON = 1200.0
DEFAULT_SOLAR_THRESHOLD_OFF = 800.0

DEFAULT_PANIC_THRESHOLD = 2000.0
DEFAULT_PANIC_DELAY = 60  # seconds

DEFAULT_MANUAL_LOCK_SECONDS = 1200  # seconds
DEFAULT_SHORT_CYCLE_ON_SECONDS = 1200  # seconds
DEFAULT_SHORT_CYCLE_OFF_SECONDS = 20  # seconds
DEFAULT_ACTION_DELAY_SECONDS = 3  # seconds
DEFAULT_UPDATE_INTERVAL = 10  # seconds
DEFAULT_HIGH_FREQUENCY_MODE = False
DEFAULT_ACTIVITY_LOGGING = False

DEFAULT_UNIFIED_ADD_THRESHOLD = 10
DEFAULT_UNIFIED_REMOVE_THRESHOLD = -10

# Learning configuration
LEARNING_TIMEOUT_SECONDS = 360
LEARNING_MIN_POWER_W = 200.0
LEARNING_MAX_POWER_W = 3000.0
LEARNING_RELATIVE_TOLERANCE = 0.5
LEARNING_EMA_ALPHA = 0.3

# Grid import tolerance for zone additions (allows some grid import when adding zones)
GRID_IMPORT_TOLERANCE_W = 350.0  # Allow up to 350W grid import when adding zones

# Zone swap configuration
ZONE_SWAP_MIN_INTERVAL_SECONDS = 300

# Climate state delays
CLIMATE_STATE_UPDATE_DELAY = 0.2

# EMA configuration
EMA_30S_ALPHA = 0.25
EMA_5M_ALPHA = 0.03
EMA_10M_ALPHA = 0.1
EMA_RESET_AFTER_OFF_SECONDS = 600

# Panic / safety configuration
PANIC_COOLDOWN_SECONDS = 120

# Comfort temperature targets (C) - 0.1 increment precision
DEFAULT_MAX_TEMP_WINTER = 21.0
DEFAULT_MIN_TEMP_SUMMER = 21.0

DEFAULT_SEASON_MODE = "heat"  # Default to cool mode
DEFAULT_ENABLE_TEMP_MODULATION = True

# Storage
# Use a literal storage key so the on-disk filename remains stable even if DOMAIN changes.
STORAGE_KEY = "solar_ac_controller"
# Bumped storage version to support migration to per-mode learned_power structure.
# Increment this integer whenever the on-disk schema changes and implement a corresponding migration.
STORAGE_VERSION = 3

# Decision engine tuning constants
DECISION_EXPORT_MARGIN_DIVISOR = 25.0
DECISION_ADD_CONFIDENCE_BASE_MAX = 40.0
DECISION_SAMPLE_BONUS_MULTIPLIER = 2.0
DECISION_SAMPLE_BONUS_MAX = 20.0
DECISION_SHORT_CYCLE_PENALTY_ADD = -30.0

DECISION_REMOVE_BASE_MAX = 60.0
DECISION_IMPORT_BASE_OFFSET = 200.0
DECISION_IMPORT_DIVISOR = 8.0
DECISION_HEAVY_IMPORT_THRESHOLD = 1500.0
DECISION_HEAVY_IMPORT_BONUS = 20.0
DECISION_SHORT_CYCLE_PENALTY_REMOVE = -40.0

DECISION_CONFIDENCE_OFFSET = 5.0

# Type definitions for better type safety
SolarACData = dict[str, Any]  # Can contain both entry data and service flags
