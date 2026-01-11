from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, CONF_AC_SWITCH, CONF_ZONES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities = [
        SolarACLearningBinarySensor(coordinator),
        SolarACPanicBinarySensor(coordinator),
        SolarACPanicCooldownBinarySensor(coordinator),
        SolarACShortCycleBinarySensor(coordinator),
        SolarACLockedBinarySensor(coordinator),
        SolarACExportingBinarySensor(coordinator),
        SolarACImportingBinarySensor(coordinator),
        SolarACMasterBinarySensor(coordinator),
    ]

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# BASE CLASS
# ---------------------------------------------------------------------------

class _BaseSolarACBinary(BinarySensorEntity):
    """Base class for all Solar AC binary sensors."""

    _attr_should_poll = False

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "solar_ac_controller")},
            "name": "Solar AC Controller",
            "configuration_url": "https://github.com/TTLucian/ha-solar-ac-controller",
        }

    async def async_added_to_hass(self):
        self.coordinator.async_add_listener(self.async_write_ha_state)


# ---------------------------------------------------------------------------
# BINARY SENSOR ENTITIES
# ---------------------------------------------------------------------------

class SolarACLearningBinarySensor(_BaseSolarACBinary):
    @property
    def name(self):
        return "Solar AC Learning Active"

    @property
    def unique_id(self):
        return "solar_ac_learning_active"

    @property
    def is_on(self):
        return bool(self.coordinator.learning_active)


class SolarACPanicBinarySensor(_BaseSolarACBinary):
    @property
    def name(self):
        return "Solar AC Panic State"

    @property
    def unique_id(self):
        return "solar_ac_panic_state"

    @property
    def is_on(self):
        # Panic is true only during the actual shed event
        return self.coordinator.last_action == "panic"


class SolarACPanicCooldownBinarySensor(_BaseSolarACBinary):
    @property
    def name(self):
        return "Solar AC Panic Cooldown"

    @property
    def unique_id(self):
        return "solar_ac_panic_cooldown"

    @property
    def is_on(self):
        ts = self.coordinator.last_panic_ts
        if not ts:
            return False
        now = dt_util.utcnow().timestamp()
        return (now - ts) < 120  # matches coordinator cooldown


class SolarACShortCycleBinarySensor(_BaseSolarACBinary):
    @property
    def name(self):
        return "Solar AC Short Cycling"

    @property
    def unique_id(self):
        return "solar_ac_short_cycling"

    @property
    def is_on(self):
        c = self.coordinator
        now = dt_util.utcnow().timestamp()

        # Use CONF_ZONES constant for consistency
        for z in c.config.get(CONF_ZONES, []):
            last = c.zone_last_changed.get(z)
            if not last:
                continue

            last_type = c.zone_last_changed_type.get(z)
            if last_type == "on":
                threshold = c.short_cycle_on_seconds
            else:
                threshold = c.short_cycle_off_seconds

            if (now - last) < threshold:
                return True

        return False


class SolarACLockedBinarySensor(_BaseSolarACBinary):
    @property
    def name(self):
        return "Solar AC Manual Lock Active"

    @property
    def unique_id(self):
        return "solar_ac_manual_lock"

    @property
    def is_on(self):
        now = dt_util.utcnow().timestamp()
        return any(
            until and until > now
            for until in self.coordinator.zone_manual_lock_until.values()
        )


class SolarACExportingBinarySensor(_BaseSolarACBinary):
    @property
    def name(self):
        return "Solar AC Exporting"

    @property
    def unique_id(self):
        return "solar_ac_exporting"

    @property
    def is_on(self):
        return bool(self.coordinator.ema_30s < 0)


class SolarACImportingBinarySensor(_BaseSolarACBinary):
    @property
    def name(self):
        return "Solar AC Importing"

    @property
    def unique_id(self):
        return "solar_ac_importing"

    @property
    def is_on(self):
        return bool(self.coordinator.ema_30s > 0)


class SolarACMasterBinarySensor(_BaseSolarACBinary):
    """Master switch sensor: ON when master is enabled, OFF when disabled."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def name(self):
        return "Solar AC Master Switch"

    @property
    def unique_id(self):
        return "solar_ac_master_switch"

    @property
    def is_on(self):
        """Return True when master is ON, False when master is OFF.

        If no master switch is configured, return True (integration runs).
        """
        ac_switch = self.coordinator.config.get(CONF_AC_SWITCH)
        if not ac_switch:
            # No physical master configured -> integration considered enabled
            return True

        switch_state_obj = self.coordinator.hass.states.get(ac_switch)
        if not switch_state_obj:
            # If entity missing, treat as OFF to be safe
            return False

        return switch_state_obj.state == "on"
