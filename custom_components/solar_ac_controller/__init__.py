from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.storage import Store
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
    CONF_MANUAL_LOCK_SECONDS,
    CONF_SHORT_CYCLE_ON_SECONDS,
    CONF_SHORT_CYCLE_OFF_SECONDS,
    CONF_PANIC_THRESHOLD,
    CONF_PANIC_DELAY,
    CONF_ACTION_DELAY_SECONDS,
    CONF_INITIAL_LEARNED_POWER,
)
from .coordinator import SolarACCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor", "binary_sensor"]


def _short_name(entity_id: str) -> str:
    """Return the short name (the part after the last dot) for an entity_id."""
    if not isinstance(entity_id, str):
        return str(entity_id)
    return entity_id.rsplit(".", 1)[-1]


async def async_setup(hass: HomeAssistant, config: dict):
    """YAML setup not supported."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Solar AC Controller from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Load persistent storage
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    stored = await store.async_load() or {}

    # Create coordinator (PASS THE ENTRY, NOT MERGED CONFIG)
    coordinator = SolarACCoordinator(hass, entry, store, stored)
    await coordinator.async_config_entry_first_refresh()

    # Store integration data
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "store": store,
    }

    # -------------------------------------------------------------------------
    # OPTIONS UPDATE LISTENER
    # -------------------------------------------------------------------------
    async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
        data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if not data:
            return

        coordinator: SolarACCoordinator = data["coordinator"]

        # Merge new options over data
        new_config = {**entry.data, **entry.options}
        coordinator.config = new_config

        # Update runtime-configurable attributes
        coordinator.manual_lock_seconds = new_config.get(
            CONF_MANUAL_LOCK_SECONDS, coordinator.manual_lock_seconds
        )
        coordinator.short_cycle_on_seconds = new_config.get(
            CONF_SHORT_CYCLE_ON_SECONDS, coordinator.short_cycle_on_seconds
        )
        coordinator.short_cycle_off_seconds = new_config.get(
            CONF_SHORT_CYCLE_OFF_SECONDS, coordinator.short_cycle_off_seconds
        )
        coordinator.action_delay_seconds = new_config.get(
            CONF_ACTION_DELAY_SECONDS, coordinator.action_delay_seconds
        )
        coordinator.panic_threshold = new_config.get(
            CONF_PANIC_THRESHOLD, coordinator.panic_threshold
        )
        coordinator.panic_delay = new_config.get(
            CONF_PANIC_DELAY, coordinator.panic_delay
        )

        # NEW: update initial learned power
        coordinator.initial_learned_power = new_config.get(
            CONF_INITIAL_LEARNED_POWER,
            coordinator.initial_learned_power,
        )

        _LOGGER.info("Solar AC options updated, refreshing coordinator")

        try:
            await coordinator.async_refresh()
        except Exception:
            _LOGGER.exception("Error refreshing coordinator after options update")

    entry.add_update_listener(_async_options_updated)

    # -------------------------------------------------------------------------
    # DEVICE REGISTRY ENTRY
    # -------------------------------------------------------------------------
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "solar_ac_controller")},
        name="Solar AC Controller",
        manufacturer="TTLucian",
        model="Solar AC Smart Controller",
        sw_version=entry.data.get("version", "0.1.3"),
        hw_version="virtual",
        configuration_url="https://github.com/TTLucian/ha-solar-ac-controller",
    )

    # -------------------------------------------------------------------------
    # FORWARD PLATFORMS
    # -------------------------------------------------------------------------
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # -------------------------------------------------------------------------
    # SERVICES
    # -------------------------------------------------------------------------

    async def handle_reset_learning(call: ServiceCall):
        try:
            await coordinator.controller.reset_learning()
        except Exception as e:
            _LOGGER.exception("Error in reset_learning service: %s", e)
            await coordinator._log(f"[SERVICE_ERROR] reset_learning {e}")

    hass.services.async_register(DOMAIN, "reset_learning", handle_reset_learning)

    async def handle_force_relearn(call: ServiceCall):
        zone = call.data.get("zone")

        # Validate zone
        if zone and zone not in coordinator.config.get("zones", []):
            await coordinator._log(f"[FORCE_RELEARN_INVALID_ZONE] {zone}")
            return

        # Reset one zone or all zones
        if zone:
            zone_name = _short_name(zone)
            coordinator.learned_power[zone_name] = 1200
            target = zone_name
        else:
            for z in coordinator.config.get("zones", []):
                zn = _short_name(z)
                coordinator.learned_power[zn] = 1200
            target = "all"

        # Reset learning state
        coordinator.samples = 0
        coordinator.learning_active = False
        coordinator.learning_zone = None
        coordinator.learning_start_time = None
        coordinator.ac_power_before = None

        await coordinator._log(f"[FORCE_RELEARN] target={target}")

        # Save updated values
        try:
            await coordinator.controller._save()
        except Exception as e:
            _LOGGER.exception("Error saving after force_relearn: %s", e)
            await coordinator._log(f"[SERVICE_ERROR] force_relearn save {e}")

    hass.services.async_register(DOMAIN, "force_relearn", handle_force_relearn)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload integration."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
