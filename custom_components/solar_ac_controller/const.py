from __future__ import annotations

DOMAIN = "solar_ac_controller"

CONF_SOLAR_SENSOR = "solar_sensor"
CONF_GRID_SENSOR = "grid_sensor"
CONF_AC_POWER_SENSOR = "ac_power_sensor"
CONF_AC_SWITCH = "ac_switch"
CONF_ZONES = "zones"

CONF_SOLAR_THRESHOLD_ON = "solar_threshold_on"
CONF_SOLAR_THRESHOLD_OFF = "solar_threshold_off"

# New panic configuration
CONF_PANIC_THRESHOLD = "panic_threshold"
CONF_PANIC_DELAY = "panic_delay"
CONF_MANUAL_LOCK_SECONDS = "manual_lock_seconds"
CONF_SHORT_CYCLE_ON_SECONDS = "short_cycle_on_seconds"
CONF_SHORT_CYCLE_OFF_SECONDS = "short_cycle_off_seconds"
CONF_ACTION_DELAY_SECONDS = "action_delay_seconds"

# Storage
STORAGE_KEY = DOMAIN
STORAGE_VERSION = 1
