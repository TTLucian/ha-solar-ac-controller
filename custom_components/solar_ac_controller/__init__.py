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
    CONF_ZONES,
    CONF_MANUAL_LOCK_SECONDS,
    CONF_SHORT_CYCLE_ON_SECONDS,
    CONF_SHORT_CYCLE_OFF_SECONDS,
    CONF_PANIC_THRESHOLD,
    CONF_PANIC_DELAY,
    CONF_ACTION_DELAY_SECONDS,
    CONF_INITIAL_LEARNED_POWER,
    CONF_ENABLE_DIAGNOSTICS,
)
from .coordinator import SolarACCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor"]


def _short_name(entity_id: str) -> str:
    """Return the trailing segment of an entity_id."""
    if not isinstance(entity_id, str):
        return str(entity_id)
    return entity_id.rsplit(".", 1)[-1]


async def async_setup(hass: HomeAssistant, config: dict):
    """YAML setup is intentionally unsupported."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Initialize the integration from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Load persistent storage
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    stored = await store.async_load() or {}

    # Coordinator handles all runtime logic
    coordinator = SolarACCoordinator(hass, entry, store, stored)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "store": store,
    }

    # ---------------------------------------------------------------------
    # OPTIONS UPDATE LISTENER
    # ---------------------------------------------------------------------
    async def _async_options_updated(hass: HomeAssistant, updated_entry: ConfigEntry):
        data = hass.data.get(DOMAIN, {}).get(updated_entry.entry_id)
        if not data:
            return

        coordinator: SolarACCoordinator = data["coordinator"]

        # Old value BEFORE applying new config
        old = bool(coordinator.config.get(CONF_ENABLE_DIAGNOSTICS, True))
        
        # Merge options over original data
        merged = {**updated_entry.data, **updated_entry.options}
        
        # New value AFTER merge
        new = bool(merged.get(CONF_ENABLE_DIAGNOSTICS, True))
        
        # If toggle changed → reload integration to add/remove diagnostics sensor
        if old != new:
            await hass.config_entries.async_reload(updated_entry.entry_id)
            return  # Reload will recreate coordinator and platforms
        
        # Apply merged config only if not reloading
        coordinator.config = merged

        # Update runtime parameters
        coordinator.manual_lock_seconds = merged.get(
            CONF_MANUAL_LOCK_SECONDS, coordinator.manual_lock_seconds
        )
        coordinator.short_cycle_on_seconds = merged.get(
            CONF_SHORT_CYCLE_ON_SECONDS, coordinator.short_cycle_on_seconds
        )
        coordinator.short_cycle_off_seconds = merged.get(
            CONF_SHORT_CYCLE_OFF_SECONDS, coordinator.short_cycle_off_seconds
        )
        coordinator.action_delay_seconds = merged.get(
            CONF_ACTION_DELAY_SECONDS, coordinator.action_delay_seconds
        )
        coordinator.panic_threshold = merged.get(
            CONF_PANIC_THRESHOLD, coordinator.panic_threshold
        )
        coordinator.panic_delay = merged.get(
            CONF_PANIC_DELAY, coordinator.panic_delay
        )
        coordinator.initial_learned_power = merged.get(
            CONF_INITIAL_LEARNED_POWER, coordinator.initial_learned_power
        )

        _LOGGER.info("Solar AC options updated; refreshing coordinator")

        try:
            await coordinator.async_refresh()
        except Exception:
            _LOGGER.exception("Coordinator refresh failed after options update")

    entry.add_update_listener(_async_options_updated)

    # ---------------------------------------------------------------------
    # DEVICE REGISTRY
    # ---------------------------------------------------------------------
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "solar_ac_controller")},
        name="Solar AC Controller",
        manufacturer="TTLucian",
        model="Solar AC Smart Controller",
        sw_version=entry.data.get("version", "0.1.3"),
        hw_version="virtual",
        configuration_url="https://github.com/TTLucian/ha-solar-ac-controller",
    )

    # ---------------------------------------------------------------------
    # PLATFORM SETUP
    # ---------------------------------------------------------------------
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ---------------------------------------------------------------------
    # SERVICES
    # ---------------------------------------------------------------------

    async def handle_reset_learning(call: ServiceCall):
        try:
            await coordinator.controller.reset_learning()
        except Exception as exc:
            _LOGGER.exception("reset_learning service failed: %s", exc)
            await coordinator._log(f"[SERVICE_ERROR] reset_learning {exc}")

    hass.services.async_register(DOMAIN, "reset_learning", handle_reset_learning)

    async def handle_force_relearn(call: ServiceCall):
        zone = call.data.get("zone")

        # Validate zone
        if zone and zone not in coordinator.config.get(CONF_ZONES, []):
            await coordinator._log(f"[FORCE_RELEARN_INVALID_ZONE] {zone}")
            return

        # Reset one or all zones
        if zone:
            zn = _short_name(zone)
            coordinator.learned_power[zn] = 1200
            target = zn
        else:
            for z in coordinator.config.get(CONF_ZONES, []):
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

        try:
            await coordinator.controller._save()
        except Exception as exc:
            _LOGGER.exception("force_relearn save failed: %s", exc)
            await coordinator._log(f"[SERVICE_ERROR] force_relearn save {exc}")

    hass.services.async_register(DOMAIN, "force_relearn", handle_force_relearn)

    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Migrate old config entries to the new format."""
    data = dict(entry.data)

    # Convert old comma-separated zone strings → list
    zones = data.get(CONF_ZONES)
    if isinstance(zones, str):
        data[CONF_ZONES] = [z.strip() for z in zones.split(",") if z.strip()]


    hass.config_entries.async_update_entry(entry, data=data)
    return True



async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload the integration."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok
