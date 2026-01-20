from __future__ import annotations

import voluptuous as vol
from typing import Any
from homeassistant import config_entries
from homeassistant.core import callback, HomeAssistant
from homeassistant.helpers.selector import selector

from .const import (
    DOMAIN,
    CONF_SOLAR_SENSOR,
    CONF_GRID_SENSOR,
    CONF_AC_POWER_SENSOR,
    CONF_AC_SWITCH,
    CONF_ZONES,
    CONF_SOLAR_THRESHOLD_ON,
    CONF_SOLAR_THRESHOLD_OFF,
    CONF_PANIC_THRESHOLD,
    CONF_PANIC_DELAY,
    CONF_MANUAL_LOCK_SECONDS,
    CONF_SHORT_CYCLE_ON_SECONDS,
    CONF_SHORT_CYCLE_OFF_SECONDS,
    CONF_ACTION_DELAY_SECONDS,
    CONF_ADD_CONFIDENCE,
    CONF_REMOVE_CONFIDENCE,
    CONF_INITIAL_LEARNED_POWER,
    CONF_ENABLE_DIAGNOSTICS,
    CONF_OUTSIDE_SENSOR,
    CONF_SEASON_MODE,
    CONF_ENABLE_TEMP_MODULATION,
    CONF_VERY_COLD_THRESHOLD,
    CONF_CHILLY_THRESHOLD,
    CONF_COMFORTABLE_THRESHOLD,
    CONF_MAX_TEMP_WINTER,
    CONF_MIN_TEMP_SUMMER,
    CONF_ZONE_TEMP_SENSORS,
    CONF_ZONE_MANUAL_POWER,
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
    DEFAULT_INITIAL_LEARNED_POWER,
    DEFAULT_SEASON_MODE,
    DEFAULT_ENABLE_TEMP_MODULATION,
    DEFAULT_VERY_COLD_THRESHOLD,
    DEFAULT_CHILLY_THRESHOLD,
    DEFAULT_COMFORTABLE_THRESHOLD,
    DEFAULT_MAX_TEMP_WINTER,
    DEFAULT_MIN_TEMP_SUMMER,
)


def _int_field(default: int, minimum: int = 0) -> vol.All:
    # vol.Default does not exist; set default in the schema, not here
    return vol.All(vol.Coerce(int), vol.Range(min=minimum))


async def _validate_zone_temp_sensors(
    hass: HomeAssistant, zones: list[str], sensors: list[str]
) -> str | None:
    """
    Validate zone-sensor mapping when temperature modulation is enabled.
    - Non-climate zones MUST have external temperature sensors
    - Climate zones MAY have external sensors (optional override)

    Returns error key if validation fails, None if OK.
    """
    if not zones:
        return None

    non_climate_missing_sensors = []

    for idx, zone_id in enumerate(zones):
        state_obj = hass.states.get(zone_id)
        if not state_obj:
            # Entity doesn't exist, assume user will create it (skip validation)
            continue

        entity_domain = state_obj.domain
        has_sensor = idx < len(sensors) and sensors[idx]

        # Non-climate entities require external sensor
        if entity_domain not in ("climate",) and not has_sensor:
            zone_name = zone_id.split(".")[-1] if zone_id else f"Zone {idx + 1}"
            non_climate_missing_sensors.append(f"{zone_name} ({entity_domain})")

    if non_climate_missing_sensors:
        # Return error key and context for UI message
        return "missing_temp_sensors_non_climate"

    return None


class SolarACConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[misc]
    """Handle the initial setup of the Solar AC Controller."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        # When invoked via reconfigure, use existing entry data/options as defaults.
        self._reconfigure_defaults: dict[str, Any] = {}
        # Store form data across steps
        self.data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Step 1: Core Setup - Zones, sensors, solar thresholds, feature toggles."""
        errors: dict[str, str] = {}
        defaults = {**self._reconfigure_defaults, **self.data}

        if user_input is not None:
            zones = user_input.get(CONF_ZONES, [])

            if not zones:
                errors["base"] = "no_zones"
            else:
                # Hysteresis validation
                solar_on = int(
                    user_input.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON)
                )
                solar_off = int(
                    user_input.get(
                        CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF
                    )
                )

                if solar_off >= solar_on:
                    errors["base"] = "invalid_solar_hysteresis"

            if not errors:
                # Store data for next steps
                self.data = {**self.data, **user_input}
                # Proceed to timing step
                return await self.async_step_timing()

        schema = vol.Schema(
            {
                # GROUP 1: What to Control
                vol.Required(
                    CONF_ZONES, default=defaults.get(CONF_ZONES, [])
                ): selector(
                    {
                        "entity": {
                            "domain": ["climate", "switch", "fan"],
                            "multiple": True,
                        }
                    }
                ),
                vol.Optional(
                    CONF_AC_SWITCH, default=defaults.get(CONF_AC_SWITCH, "")
                ): selector({"entity": {"domain": "switch"}}),
                # GROUP 2: Sensors
                vol.Required(
                    CONF_SOLAR_SENSOR, default=defaults.get(CONF_SOLAR_SENSOR)
                ): selector({"entity": {"domain": "sensor"}}),
                vol.Required(
                    CONF_GRID_SENSOR, default=defaults.get(CONF_GRID_SENSOR)
                ): selector({"entity": {"domain": "sensor"}}),
                vol.Required(
                    CONF_AC_POWER_SENSOR, default=defaults.get(CONF_AC_POWER_SENSOR)
                ): selector({"entity": {"domain": "sensor"}}),
                # GROUP 3: When to Activate
                vol.Optional(
                    CONF_SOLAR_THRESHOLD_ON,
                    default=int(
                        defaults.get(
                            CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON
                        )
                    ),
                ): _int_field(int(DEFAULT_SOLAR_THRESHOLD_ON), minimum=0),
                vol.Optional(
                    CONF_SOLAR_THRESHOLD_OFF,
                    default=int(
                        defaults.get(
                            CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF
                        )
                    ),
                ): _int_field(int(DEFAULT_SOLAR_THRESHOLD_OFF), minimum=0),
                # GROUP 4: How Aggressive to Add/Remove
                vol.Optional(
                    CONF_ADD_CONFIDENCE,
                    default=int(
                        defaults.get(CONF_ADD_CONFIDENCE, DEFAULT_ADD_CONFIDENCE)
                    ),
                ): _int_field(int(DEFAULT_ADD_CONFIDENCE), minimum=0),
                vol.Optional(
                    CONF_REMOVE_CONFIDENCE,
                    default=int(
                        defaults.get(CONF_REMOVE_CONFIDENCE, DEFAULT_REMOVE_CONFIDENCE)
                    ),
                ): _int_field(int(DEFAULT_REMOVE_CONFIDENCE), minimum=0),
                vol.Optional(
                    CONF_INITIAL_LEARNED_POWER,
                    default=int(
                        defaults.get(
                            CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER
                        )
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=0)),
                # GROUP 6: Optional Features
                vol.Optional(
                    CONF_ENABLE_TEMP_MODULATION,
                    default=bool(
                        defaults.get(
                            CONF_ENABLE_TEMP_MODULATION, DEFAULT_ENABLE_TEMP_MODULATION
                        )
                    ),
                ): bool,
                vol.Optional(
                    CONF_ENABLE_DIAGNOSTICS,
                    default=bool(defaults.get(CONF_ENABLE_DIAGNOSTICS, False)),
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_timing(self, user_input: dict[str, Any] | None = None):
        """Step 2: Timing & Protection - Delays and guards."""
        errors: dict[str, str] = {}
        defaults = {**self._reconfigure_defaults, **self.data}

        if user_input is not None:
            panic_th = int(
                user_input.get(CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD)
            )
            solar_on = int(
                self.data.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON)
            )

            if panic_th <= solar_on:
                errors["base"] = "panic_too_low"

            if not errors:
                self.data = {**self.data, **user_input}

                # Proceed to comfort step if temp modulation enabled, otherwise create entry
                if self.data.get(CONF_ENABLE_TEMP_MODULATION):
                    return await self.async_step_comfort()
                else:
                    return self.async_create_entry(
                        title="Solar AC Controller", data=self.data
                    )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ACTION_DELAY_SECONDS,
                    default=int(
                        defaults.get(
                            CONF_ACTION_DELAY_SECONDS, DEFAULT_ACTION_DELAY_SECONDS
                        )
                    ),
                ): _int_field(int(DEFAULT_ACTION_DELAY_SECONDS), minimum=0),
                vol.Optional(
                    CONF_MANUAL_LOCK_SECONDS,
                    default=int(
                        defaults.get(
                            CONF_MANUAL_LOCK_SECONDS, DEFAULT_MANUAL_LOCK_SECONDS
                        )
                    ),
                ): _int_field(int(DEFAULT_MANUAL_LOCK_SECONDS), minimum=0),
                vol.Optional(
                    CONF_SHORT_CYCLE_ON_SECONDS,
                    default=int(
                        defaults.get(
                            CONF_SHORT_CYCLE_ON_SECONDS, DEFAULT_SHORT_CYCLE_ON_SECONDS
                        )
                    ),
                ): _int_field(int(DEFAULT_SHORT_CYCLE_ON_SECONDS), minimum=0),
                vol.Optional(
                    CONF_SHORT_CYCLE_OFF_SECONDS,
                    default=int(
                        defaults.get(
                            CONF_SHORT_CYCLE_OFF_SECONDS,
                            DEFAULT_SHORT_CYCLE_OFF_SECONDS,
                        )
                    ),
                ): _int_field(int(DEFAULT_SHORT_CYCLE_OFF_SECONDS), minimum=0),
                vol.Optional(
                    CONF_PANIC_THRESHOLD,
                    default=int(
                        defaults.get(CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD)
                    ),
                ): _int_field(int(DEFAULT_PANIC_THRESHOLD), minimum=0),
                vol.Optional(
                    CONF_PANIC_DELAY,
                    default=int(defaults.get(CONF_PANIC_DELAY, DEFAULT_PANIC_DELAY)),
                ): _int_field(int(DEFAULT_PANIC_DELAY), minimum=0),
            }
        )

        return self.async_show_form(
            step_id="timing",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_comfort(self, user_input: dict[str, Any] | None = None):
        """Step 3: Comfort-Based Zone Control (conditional on ENABLE_TEMP_MODULATION)."""
        errors: dict[str, str] = {}
        defaults = {**self._reconfigure_defaults, **self.data}

        if user_input is not None:
            zones = self.data.get(CONF_ZONES, [])
            zone_temp_sensors = user_input.get(CONF_ZONE_TEMP_SENSORS, [])
            zone_manual_power = user_input.get(CONF_ZONE_MANUAL_POWER) or ""

            # Normalize/pad sensor list to zone length, allow blanks for climate zones
            if zone_temp_sensors and len(zone_temp_sensors) < len(zones):
                zone_temp_sensors = list(zone_temp_sensors) + [""] * (
                    len(zones) - len(zone_temp_sensors)
                )
            if len(zone_temp_sensors) > len(zones):
                zone_temp_sensors = zone_temp_sensors[: len(zones)]

            # Validate non-climate zones have external sensors
            validation_error = await _validate_zone_temp_sensors(
                self.hass, zones, zone_temp_sensors
            )
            if validation_error:
                errors["base"] = validation_error

            if not errors:
                self.data = {**self.data, **user_input}
                return self.async_create_entry(
                    title="Solar AC Controller", data=self.data
                )

        zone_manual_default = defaults.get(CONF_ZONE_MANUAL_POWER, "")
        if isinstance(zone_manual_default, (list, tuple)):
            zone_manual_default = ", ".join(str(v) for v in zone_manual_default)
        elif zone_manual_default is None:
            zone_manual_default = ""
        else:
            zone_manual_default = str(zone_manual_default)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ZONE_TEMP_SENSORS,
                    default=defaults.get(CONF_ZONE_TEMP_SENSORS, []),
                ): selector(
                    {
                        "entity": {
                            "domain": "sensor",
                            "device_class": ["temperature"],
                            "multiple": True,
                        }
                    }
                ),
                vol.Optional(
                    CONF_ZONE_MANUAL_POWER,
                    default=zone_manual_default,
                ): selector({"text": {"multiline": False}}),
                vol.Optional(
                    CONF_MAX_TEMP_WINTER,
                    default=float(
                        defaults.get(CONF_MAX_TEMP_WINTER, DEFAULT_MAX_TEMP_WINTER)
                    ),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_MIN_TEMP_SUMMER,
                    default=float(
                        defaults.get(CONF_MIN_TEMP_SUMMER, DEFAULT_MIN_TEMP_SUMMER)
                    ),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_VERY_COLD_THRESHOLD,
                    default=float(
                        defaults.get(CONF_VERY_COLD_THRESHOLD, DEFAULT_VERY_COLD_THRESHOLD)
                    ),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_CHILLY_THRESHOLD,
                    default=float(
                        defaults.get(CONF_CHILLY_THRESHOLD, DEFAULT_CHILLY_THRESHOLD)
                    ),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_COMFORTABLE_THRESHOLD,
                    default=float(
                        defaults.get(CONF_COMFORTABLE_THRESHOLD, DEFAULT_COMFORTABLE_THRESHOLD)
                    ),
                ): vol.Coerce(float),
            }
        )

        return self.async_show_form(
            step_id="comfort",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_import(self, user_input: dict[str, Any]):
        return await self.async_step_user(user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        """Handle reconfigure flow by seeding defaults from existing entry."""
        entry_id = self.context.get("entry_id")
        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        if entry:
            self._reconfigure_defaults = {**entry.data, **entry.options}
            self.context["title_placeholders"] = {"name": entry.title}
        else:
            self._reconfigure_defaults = {}

        return await self.async_step_user(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return SolarACOptionsFlowHandler(config_entry)


class SolarACOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle runtime configuration changes."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        self.entry = config_entry
        self.data: dict[str, Any] = {}

    @property
    def _current(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """First step of options (same as initial user setup)."""
        errors: dict[str, str] = {}
        defaults = self._current

        if user_input is not None:
            zones = user_input.get(CONF_ZONES, [])

            if not zones:
                errors["base"] = "no_zones"
            else:
                # Hysteresis validation
                solar_on = int(
                    user_input.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON)
                )
                solar_off = int(
                    user_input.get(
                        CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF
                    )
                )

                if solar_off >= solar_on:
                    errors["base"] = "invalid_solar_hysteresis"

            if not errors:
                self.data = {**self.data, **user_input}
                return await self.async_step_timing()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ZONES, default=defaults.get(CONF_ZONES, [])
                ): selector(
                    {
                        "entity": {
                            "domain": ["climate", "switch", "fan"],
                            "multiple": True,
                        }
                    }
                ),
                vol.Required(
                    CONF_SOLAR_SENSOR, default=defaults.get(CONF_SOLAR_SENSOR)
                ): selector({"entity": {"domain": "sensor"}}),
                vol.Required(
                    CONF_GRID_SENSOR, default=defaults.get(CONF_GRID_SENSOR)
                ): selector({"entity": {"domain": "sensor"}}),
                vol.Required(
                    CONF_AC_POWER_SENSOR, default=defaults.get(CONF_AC_POWER_SENSOR)
                ): selector({"entity": {"domain": "sensor"}}),
                vol.Optional(
                    CONF_AC_SWITCH, default=defaults.get(CONF_AC_SWITCH, "")
                ): selector({"entity": {"domain": "switch"}}),
                vol.Optional(
                    CONF_SOLAR_THRESHOLD_ON,
                    default=int(
                        defaults.get(
                            CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON
                        )
                    ),
                ): _int_field(int(DEFAULT_SOLAR_THRESHOLD_ON), minimum=0),
                vol.Optional(
                    CONF_SOLAR_THRESHOLD_OFF,
                    default=int(
                        defaults.get(
                            CONF_SOLAR_THRESHOLD_OFF, DEFAULT_SOLAR_THRESHOLD_OFF
                        )
                    ),
                ): _int_field(int(DEFAULT_SOLAR_THRESHOLD_OFF), minimum=0),
                vol.Optional(
                    CONF_ADD_CONFIDENCE,
                    default=int(
                        defaults.get(CONF_ADD_CONFIDENCE, DEFAULT_ADD_CONFIDENCE)
                    ),
                ): _int_field(int(DEFAULT_ADD_CONFIDENCE), minimum=0),
                vol.Optional(
                    CONF_REMOVE_CONFIDENCE,
                    default=int(
                        defaults.get(CONF_REMOVE_CONFIDENCE, DEFAULT_REMOVE_CONFIDENCE)
                    ),
                ): _int_field(int(DEFAULT_REMOVE_CONFIDENCE), minimum=0),
                vol.Optional(
                    CONF_INITIAL_LEARNED_POWER,
                    default=int(
                        defaults.get(
                            CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER
                        )
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=0)),
                vol.Optional(
                    CONF_ENABLE_TEMP_MODULATION,
                    default=bool(
                        defaults.get(
                            CONF_ENABLE_TEMP_MODULATION, DEFAULT_ENABLE_TEMP_MODULATION
                        )
                    ),
                ): bool,
                vol.Optional(
                    CONF_ENABLE_DIAGNOSTICS,
                    default=bool(defaults.get(CONF_ENABLE_DIAGNOSTICS, False)),
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_timing(self, user_input: dict[str, Any] | None = None):
        """Step 2: Timing & Protection."""
        errors: dict[str, str] = {}
        defaults = {**self._current, **self.data}

        if user_input is not None:
            panic_th = int(
                user_input.get(CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD)
            )
            solar_on = int(
                self.data.get(CONF_SOLAR_THRESHOLD_ON, DEFAULT_SOLAR_THRESHOLD_ON)
            )

            if panic_th <= solar_on:
                errors["base"] = "panic_too_low"

            if not errors:
                self.data = {**self.data, **user_input}

                # Decide next step based on feature toggles
                if self.data.get(CONF_ENABLE_TEMP_MODULATION):
                    return await self.async_step_comfort()
                else:
                    # Create entry if no conditional steps
                    return self.async_create_entry(title="", data=self.data)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ACTION_DELAY_SECONDS,
                    default=int(
                        defaults.get(
                            CONF_ACTION_DELAY_SECONDS, DEFAULT_ACTION_DELAY_SECONDS
                        )
                    ),
                ): _int_field(int(DEFAULT_ACTION_DELAY_SECONDS), minimum=0),
                vol.Optional(
                    CONF_MANUAL_LOCK_SECONDS,
                    default=int(
                        defaults.get(
                            CONF_MANUAL_LOCK_SECONDS, DEFAULT_MANUAL_LOCK_SECONDS
                        )
                    ),
                ): _int_field(int(DEFAULT_MANUAL_LOCK_SECONDS), minimum=0),
                vol.Optional(
                    CONF_SHORT_CYCLE_ON_SECONDS,
                    default=int(
                        defaults.get(
                            CONF_SHORT_CYCLE_ON_SECONDS, DEFAULT_SHORT_CYCLE_ON_SECONDS
                        )
                    ),
                ): _int_field(int(DEFAULT_SHORT_CYCLE_ON_SECONDS), minimum=0),
                vol.Optional(
                    CONF_SHORT_CYCLE_OFF_SECONDS,
                    default=int(
                        defaults.get(
                            CONF_SHORT_CYCLE_OFF_SECONDS,
                            DEFAULT_SHORT_CYCLE_OFF_SECONDS,
                        )
                    ),
                ): _int_field(int(DEFAULT_SHORT_CYCLE_OFF_SECONDS), minimum=0),
                vol.Optional(
                    CONF_PANIC_THRESHOLD,
                    default=int(
                        defaults.get(CONF_PANIC_THRESHOLD, DEFAULT_PANIC_THRESHOLD)
                    ),
                ): _int_field(int(DEFAULT_PANIC_THRESHOLD), minimum=0),
                vol.Optional(
                    CONF_PANIC_DELAY,
                    default=int(defaults.get(CONF_PANIC_DELAY, DEFAULT_PANIC_DELAY)),
                ): _int_field(int(DEFAULT_PANIC_DELAY), minimum=0),
            }
        )

        return self.async_show_form(
            step_id="timing",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_comfort(self, user_input: dict[str, Any] | None = None):
        """Step 3: Comfort-Based Zone Control (conditional)."""
        errors: dict[str, str] = {}
        defaults = {**self._current, **self.data}

        if user_input is not None:
            zones = self.data.get(CONF_ZONES, [])
            zone_temp_sensors = user_input.get(CONF_ZONE_TEMP_SENSORS, [])
            zone_manual_power = user_input.get(CONF_ZONE_MANUAL_POWER) or ""

            # Normalize/pad sensor list to zone length, allow blanks for climate zones
            if zone_temp_sensors and len(zone_temp_sensors) < len(zones):
                zone_temp_sensors = list(zone_temp_sensors) + [""] * (
                    len(zones) - len(zone_temp_sensors)
                )
            if len(zone_temp_sensors) > len(zones):
                zone_temp_sensors = zone_temp_sensors[: len(zones)]

            # Validate non-climate zones have external sensors
            validation_error = await _validate_zone_temp_sensors(
                self.hass, zones, zone_temp_sensors
            )
            if validation_error:
                errors["base"] = validation_error

            if not errors:
                self.data = {**self.data, **user_input}
                return self.async_create_entry(title="", data=self.data)

        zone_manual_default = defaults.get(CONF_ZONE_MANUAL_POWER, "")
        if isinstance(zone_manual_default, (list, tuple)):
            zone_manual_default = ", ".join(str(v) for v in zone_manual_default)
        elif zone_manual_default is None:
            zone_manual_default = ""
        else:
            zone_manual_default = str(zone_manual_default)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ZONE_TEMP_SENSORS,
                    default=defaults.get(CONF_ZONE_TEMP_SENSORS, []),
                ): selector(
                    {
                        "entity": {
                            "domain": "sensor",
                            "device_class": ["temperature"],
                            "multiple": True,
                        }
                    }
                ),
                vol.Optional(
                    CONF_ZONE_MANUAL_POWER,
                    default=zone_manual_default,
                ): selector({"text": {"multiline": False}}),
                vol.Optional(
                    CONF_MAX_TEMP_WINTER,
                    default=float(
                        defaults.get(CONF_MAX_TEMP_WINTER, DEFAULT_MAX_TEMP_WINTER)
                    ),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_MIN_TEMP_SUMMER,
                    default=float(
                        defaults.get(CONF_MIN_TEMP_SUMMER, DEFAULT_MIN_TEMP_SUMMER)
                    ),
                ): vol.Coerce(float),
            }
        )

        return self.async_show_form(
            step_id="comfort",
            data_schema=schema,
            errors=errors,
        )
