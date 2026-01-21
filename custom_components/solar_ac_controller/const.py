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

# Behavioral tuning
CONF_PANIC_THRESHOLD = "panic_threshold"
CONF_PANIC_DELAY = "panic_delay"
CONF_MANUAL_LOCK_SECONDS = "manual_lock_seconds"
CONF_SHORT_CYCLE_ON_SECONDS = "short_cycle_on_seconds"
CONF_SHORT_CYCLE_OFF_SECONDS = "short_cycle_off_seconds"
CONF_ACTION_DELAY_SECONDS = "action_delay_seconds"

# Unified confidence thresholds (points)
CONF_ADD_CONFIDENCE = "add_confidence"
CONF_REMOVE_CONFIDENCE = "remove_confidence"

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

DEFAULT_ADD_CONFIDENCE = 25
DEFAULT_REMOVE_CONFIDENCE = 10


# Comfort temperature targets (C) - 0.1 increment precision
DEFAULT_MAX_TEMP_WINTER = 21.0
DEFAULT_MIN_TEMP_SUMMER = 21.0

DEFAULT_SEASON_MODE = "cool"  # Default to cool mode
DEFAULT_ENABLE_TEMP_MODULATION = True

# Storage
# Use a literal storage key so the on-disk filename remains stable even if DOMAIN changes.
STORAGE_KEY = "solar_ac_controller"
# Bumped storage version to support migration to per-mode learned_power structure.
# Increment this integer whenever the on-disk schema changes and implement a corresponding migration.
STORAGE_VERSION = 3

# Type definitions for better type safety
SolarACData = dict[str, Any]  # Can contain both entry data and service flags
