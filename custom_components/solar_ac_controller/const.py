from __future__ import annotations

"""Constants for the Solar AC Controller integration."""

# When changing the on-disk schema, increment STORAGE_VERSION and add a migration.
# Bump STORAGE_VERSION whenever the structure of the stored payload changes
# (for example: renaming keys, changing types, or moving from numeric to dict shapes).
# Keep a short note in the migration function describing the change and the target version.
# If you want the on-disk filename to remain stable across domain renames, keep STORAGE_KEY
# as a literal string (below) rather than deriving it from DOMAIN.
# Number of backup files to retain for storage (optional).
STORAGE_BACKUP_KEEP = 5

DOMAIN = "solar_ac_controller"

# Core configuration keys
CONF_SOLAR_SENSOR = "solar_sensor"
CONF_GRID_SENSOR = "grid_sensor"
CONF_AC_POWER_SENSOR = "ac_power_sensor"
CONF_AC_SWITCH = "ac_switch"
CONF_ZONES = "zones"

# Solar thresholds (W)
CONF_SOLAR_THRESHOLD_ON = "solar_threshold_on"
CONF_SOLAR_THRESHOLD_OFF = "solar_threshold_off"

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

# Enable diagnostics sensor toggle
CONF_ENABLE_DIAGNOSTICS = "enable_diagnostics_sensor"

# Default initial learned power used when no value is configured
DEFAULT_INITIAL_LEARNED_POWER = 1200.0

# Storage
# Use a literal storage key so the on-disk filename remains stable even if DOMAIN changes.
STORAGE_KEY = "solar_ac_controller"
# Bumped storage version to support migration to per-mode learned_power structure.
# Increment this integer whenever the on-disk schema changes and implement a corresponding migration.
STORAGE_VERSION = 2
