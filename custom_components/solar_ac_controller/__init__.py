from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.storage import Store, async_migrate
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import async_get_integration
from homeassistant.helpers.typing import ConfigType

from .coordinator import SolarACCoordinator
from .const import (
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
    CONF_INITIAL_LEARNED_POWER,
    DEFAULT_INITIAL_LEARNED_POWER,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor"]


async def _async_migrate_data(
    old_major: int,
    old_minor: int,
    old_data: dict | None,
    initial_lp: float = DEFAULT_INITIAL_LEARNED_POWER,
) -> dict:
    """Standard migration callback for the HA Store helper.

    Converts legacy numeric learned_power values into per-mode dicts and
    ensures partially migrated dicts have default/heat/cool keys.
    """
    _LOGGER.info("Migrating storage from v%s.%s to v%s", old_major, old_minor, STORAGE_VERSION)

    # 1. Handle None or non-dict root data
    if not isinstance(old_data, dict):
        _LOGGER.debug("Storage data is empty or not a dict; initializing fresh structure")
        return {"learned_power": {}, "samples": 0}

    # 2. Safely extract learned_power
    learned_power = old_data.get("learned_power")
    if learned_power is None:
        learned_power = {}

    if not isinstance(learned_power, dict):
        _LOGGER.warning("learned_power in storage was not a dictionary; resetting it")
        learned_power = {}

    modified = False

    # 3. Iterate with safe type checks
    for zone, val in list(learned_power.items()):
        if val is None:
            # Clean up corrupted entries
            learned_power[zone] = {"default": initial_lp, "heat": initial_lp, "cool": initial_lp}
            modified = True
        elif isinstance(val, (int, float)):
            # Convert legacy flat values to mode-based dicts
            learned_power[zone] = {"default": float(val), "heat": float(val), "cool": float(val)}
            modified = True
        elif isinstance(val, dict):
            # Ensure all required keys exist in nested dictionaries
            zone_modified = False
            for mode in ("default", "heat", "cool"):
                if val.get(mode) is None:
                    # Prefer 'default' value if available, otherwise fallback to initial_lp
                    val[mode] = val.get("default") if val.get("default") is not None else initial_lp
                    zone_modified = True

            if zone_modified:
                learned_power[zone] = val
                modified = True

    # 4. Final payload reconstruction
    if modified:
        old_data["learned_power"] = learned_power
        # Ensure samples is a valid integer, defaulting to 0
        try:
            old_data["samples"] = int(old_data.get("samples", 0) or 0)
        except (TypeError, ValueError):
            old_data["samples"] = 0

    return old_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register storage migration early so HA can call it during its migration step."""
    async def migrate_wrapper(old_major: int, old_minor: int, data: dict | None) -> dict:
        # data could be None if the file doesn't exist yet
        data_payload = data if data is not None else {}
        initial_lp = float(data_payload.get("initial_learned_power", DEFAULT_INITIAL_LEARNED_POWER))
        return await _async_migrate_data(old_major, old_minor, data_payload, initial_lp)

    try:
        await async_migrate(hass, STORAGE_KEY, 1, STORAGE_VERSION, migrate_wrapper)
        _LOGGER.debug("Registered async_migrate for %s", STORAGE_KEY)
    except NotImplementedError:
        _LOGGER.warning("Host storage does not support async_migrate; using fallback in setup_entry")
    except Exception:
        _LOGGER.exception("Failed to register async_migrate")

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Solar AC Controller from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    integration = await async_get_integration(hass, DOMAIN)
    version = integration.version
    initial_lp = float(entry.options.get(CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER))

    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)

    try:
        stored_data = await store.async_load()
    except Exception:
        _LOGGER.exception("Critical error loading integration storage")
        stored_data = None

    # Explicit fallback migration
    try:
        # _async_migrate_data now handles None internally
        migrated = await _async_migrate_data(0, 0, stored_data, initial_lp)

        if migrated != stored_data:
            _LOGGER.debug("Data migration changed payload; saving to storage")
            await store.async_save(migrated)
            stored_data = migrated
            _LOGGER.info("Storage migrated and saved for %s", STORAGE_KEY)
    except Exception:
        _LOGGER.exception("Error while attempting explicit migration fallback")
        if stored_data is None:
            stored_data = {"learned_power": {}, "samples": 0}

    coordinator = SolarACCoordinator(
        hass,
        entry,
        store,
        stored_data,
        version=version,
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
    }

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "solar_ac_controller")},
        name="Solar AC Controller",
        manufacturer="TTLucian",
        model="Solar AC Smart Controller",
        sw_version=version,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.add_update_listener(async_reload_entry)

    async def handle_reset_learning(call: ServiceCall):
        if hasattr(coordinator, "controller"):
            await coordinator.controller.reset_learning()

    hass.services.async_register(DOMAIN, "reset_learning", handle_reset_learning)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    if not hass.data.get(DOMAIN):
        if hass.services.has_service(DOMAIN, "reset_learning"):
            hass.services.async_remove(DOMAIN, "reset_learning")

    return unload_ok
