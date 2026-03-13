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
CONF_PV_CAPACITY_W = "pv_capacity_w"
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
CONF_ACTIVITY_LOGGING = "activity_logging"

# Compressor / hardware tuning
CONF_COMPRESSOR_RAMP_SECONDS = "compressor_ramp_seconds"
DEFAULT_COMPRESSOR_RAMP_SECONDS = (
    600  # seconds (10 minutes) - multisplit conservative default
)

# High-level tuning: single aggressiveness slider (0.0 conservative -> 1.0 aggressive)
CONF_AGGRESSIVENESS = "aggressiveness"
DEFAULT_AGGRESSIVENESS = 0.5

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
DEFAULT_SOLAR_THRESHOLD_OFF = 500.0
DEFAULT_PV_CAPACITY_W: int = 0  # 0 = not configured; feature disabled when 0

DEFAULT_PANIC_THRESHOLD = 2000.0
DEFAULT_PANIC_DELAY = 60  # seconds

DEFAULT_MANUAL_LOCK_SECONDS = 1200  # seconds
DEFAULT_SHORT_CYCLE_ON_SECONDS = 1200  # seconds
DEFAULT_SHORT_CYCLE_OFF_SECONDS = 20  # seconds
DEFAULT_ACTION_DELAY_SECONDS = 3  # seconds
DEFAULT_UPDATE_INTERVAL = 10  # seconds
DEFAULT_ACTIVITY_LOGGING = False

DEFAULT_UNIFIED_ADD_THRESHOLD = 10
DEFAULT_UNIFIED_REMOVE_THRESHOLD = -10

# Learning configuration
LEARNING_TIMEOUT_SECONDS = 360
LEARNING_MIN_POWER_W = 200.0
LEARNING_MAX_POWER_W = 3000.0
LEARNING_RELATIVE_TOLERANCE = 0.5
LEARNING_EMA_ALPHA = 0.3


# Zone swap configuration
ZONE_SWAP_MIN_INTERVAL_SECONDS = 300

# Climate state delays
CLIMATE_STATE_UPDATE_DELAY = 0.2

# EMA configuration
EMA_30S_ALPHA = 0.25
EMA_5M_ALPHA = 0.03
EMA_10M_ALPHA = 0.1
EMA_RESET_AFTER_OFF_SECONDS = 600

# Solar EMA alphas for cloud / trend detection
# Fast (~33 s time constant at 10 s cycle) tracks rapid production drops.
# Slow matches the grid slow EMA for an apples-to-apples spread comparison.
SOLAR_EMA_FAST_ALPHA: float = 0.15
SOLAR_EMA_SLOW_ALPHA: float = 0.03

# Solar slope thresholds (W) used by the decision engine
# "Cloud": fast EMA has dropped more than this far below slow EMA → penalise adds.
SOLAR_SLOPE_CLOUD_THRESHOLD_W: float = 150.0
# "Stable solar": the fast-slow spread is within ±this → grid import is a household
# load spike, not a cloud-driven supply drop → suppress zone-removal confidence.
SOLAR_STABLE_THRESHOLD_W: float = 100.0
# Maximum magnitudes for the two adjustments
SOLAR_CLOUD_ADD_PENALTY_MAG: float = 20.0
# Transient load-spike suppression magnitude reduced to avoid over-suppressing
# genuine sustained imports.  Only fires when import_power < ceiling (below).
SOLAR_TRANSIENT_REMOVE_SUPPRESS_MAG: float = 8.0
# Ceiling (W): if import_power is above this the load is likely not a transient.
SOLAR_TRANSIENT_IMPORT_CEILING_W: float = 400.0

# PV-fraction bonuses (only active when pv_capacity_w > 0)
# Solar fraction at which bonuses start to ramp up (e.g. 0.6 = 60 % of rated capacity).
SOLAR_FRACTION_BONUS_THRESHOLD: float = 0.6
# Maximum add-confidence bonus at solar_fraction = 1.0 (scaled by bonus_scale).
SOLAR_FRACTION_ADD_BONUS_MAX: float = 12.0
# Maximum remove-confidence suppression at solar_fraction = 1.0 (scaled by penalty_scale).
# Kept small so genuine sustained imports can still push remove_conf over threshold.
SOLAR_FRACTION_REMOVE_SUPPRESS_MAX: float = 5.0

# Panic / safety configuration
PANIC_COOLDOWN_SECONDS = 120

# Master switch command grace period (seconds)
MASTER_SWITCH_COMMAND_GRACE_PERIOD = 10

# Manual override detection window (seconds)
MANUAL_OVERRIDE_DETECTION_WINDOW = 120

# Logbook throttling (seconds)
LOGBOOK_THROTTLE_SECONDS = 3.0

# Learning stabilization reading count
STABILIZATION_READING_COUNT = 24

# Power readings buffer limit (5 minutes at 5s intervals)
POWER_READINGS_MAX_ENTRIES = 60

# Zone action history ring buffer size (records per zone, persisted to storage)
MAX_ZONE_HISTORY_RECORDS = 20

# Grace period (seconds) for context-based authorship check.
# If the HA context ID match fails (entity doesn't propagate context), fall back
# to this tight window to still reject clearly-unrelated state changes.
COMMAND_CONTEXT_GRACE_SECONDS = 30

# Balanced state log interval (seconds)
BALANCED_LOG_INTERVAL_SECONDS = 600

# Stale tracking data cleanup interval (seconds)
STALE_TRACKING_CLEANUP_INTERVAL_SECONDS = 3600

# Idle compressor power learning
# When all zones are off and the compressor switch is on we sample ac_power
# to build an EMA of the standby/idle draw (fan, control board, etc.).
IDLE_POWER_EMA_ALPHA = 0.05  # Slow alpha – idle draw is physically stable
IDLE_POWER_MAX_W = 50.0  # Samples above this are rejected (zone still active)
IDLE_POWER_SETTLE_SECONDS = 120  # Wait 2 min after last zone-off before sampling
IDLE_POWER_MIN_SAMPLES = 6  # ~1 min of data required before value is trusted
SPINDOWN_THRESHOLD_W = 30.0  # ac_power − idle > this → compressor still spinning down
STRAY_ZONE_THRESHOLD_W = (
    80.0  # ac_power − idle > this with 0 zones → stray zone warning
)

# Comfort temperature targets (C) - 0.1 increment precision
DEFAULT_MAX_TEMP_WINTER = 21.0
DEFAULT_MIN_TEMP_SUMMER = 21.0

DEFAULT_SEASON_MODE = "heat"  # Default to heat mode
DEFAULT_ENABLE_TEMP_MODULATION = True

# Storage
# Use a literal storage key so the on-disk filename remains stable even if DOMAIN changes.
STORAGE_KEY = "solar_ac_controller"
# Bumped storage version to support migration to per-mode learned_power structure.
# Increment this integer whenever the on-disk schema changes and implement a corresponding migration.
STORAGE_VERSION = 1

# Decision engine tuning constants
DECISION_EXPORT_MARGIN_DIVISOR = 40.0
DECISION_ADD_CONFIDENCE_BASE_MAX = 40.0
DECISION_SAMPLE_BONUS_MULTIPLIER = 2.0
DECISION_SAMPLE_BONUS_MAX = 20.0
DECISION_SHORT_CYCLE_PENALTY_ADD = -30.0

DECISION_REMOVE_BASE_MAX = 60.0
DECISION_IMPORT_BASE_OFFSET = 200.0
DECISION_IMPORT_DIVISOR = 25.0
DECISION_HEAVY_IMPORT_THRESHOLD = 1500.0
DECISION_HEAVY_IMPORT_BONUS = 20.0
DECISION_SHORT_CYCLE_PENALTY_REMOVE = -40.0

DECISION_CONFIDENCE_OFFSET = 5.0

# Additional decision tuning constants
DECISION_EMA_BONUS_MULTIPLIER = 8.0
DECISION_COMP_PENALTY_MAG = 40.0
DECISION_LEARN_PENALTY_MAG = 100.0
DECISION_AC_STABILITY_THRESHOLD_W = 50.0
DECISION_AC_STABILITY_BONUS = 15.0
DECISION_STABILITY_DENOM_MIN = 100.0
DECISION_SWAP_BUFFER_W = 200.0
# Variability normalization divisor used when scaling export margin divisor
DECISION_VARIABILITY_DIVISOR = 250.0
# Maximum import tolerance (W) derived from aggressiveness: tolerance = a * MAX
# At a=0.0: 0 W (strict), a=0.5: 350 W, a=1.0: 700 W
DECISION_IMPORT_TOLERANCE_MAX_W = 700.0

# Raw clamping ranges for internal confidence math
DECISION_RAW_MIN = -100.0
DECISION_RAW_MAX = 200.0

# Final clamping ranges exposed to other systems (0-100 points)
DECISION_FINAL_MIN = 0.0
DECISION_FINAL_MAX = 100.0

# Zone temperature stability margins (C)
DECISION_ZONE_TEMP_MARGIN = 0.5
DECISION_ZONE_NEEDS_HEATING_DIFF = 1.0

# Type definitions for better type safety
SolarACData = dict[str, Any]  # Can contain both entry data and service flags
