from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

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
    """Normalize and migrate stored data into the new per-mode structure."""
    if not isinstance(old_data, dict):
        return {"learned_power": {}, "samples": 0}

    learned_power = old_data.get("learned_power", {})
    if not isinstance(learned_power, dict):
        learned_power = {}

    modified = False

    for zone, val in list(learned_power.items()):
        if val is None:
            learned_power[zone] = {"default": initial_lp, "heat": initial_lp, "cool": initial_lp}
            modified = True
        elif isinstance(val, (int, float)):
            v = float(val)
            learned_power[zone] = {"default": v, "heat": v, "cool": v}
            modified = True
        elif isinstance(val, dict):
            default = val.get("default")
            for mode in ("default", "heat", "cool"):
                if val.get(mode) is None:
                    val[mode] = default if default is not None else initial_lp
                    modified = True
            learned_power[zone] = val
        else:
            learned_power[zone] = {"default": initial_lp, "heat": initial_lp, "cool": initial_lp}
            modified = True

    if modified:
        old_data["learned_power"] = learned_power
        try:
            old_data["samples"] = int(old_data.get("samples", 0) or 0)
        except Exception:
            old_data["samples"] = 0

    return old_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Solar AC Controller from a config entry.

    This version intentionally does NOT call device_registry.async_get_or_create.
    Entities themselves expose minimal device_info (identifiers only) if desired.
    """
    hass.data.setdefault(DOMAIN, {})

    # Safely resolve integration version to a plain string or None
    integration = await async_get_integration(hass, DOMAIN)
    version = None
    if integration is not None and getattr(integration, "version", None) is not None:
        try:
            version = str(integration.version)
        except Exception:
            _LOGGER.debug("Failed to stringify integration.version; using None for version")
            version = None

    initial_lp = float(
        entry.options.get(
            CONF_INITIAL_LEARNED_POWER,
            entry.data.get(CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER),
        )
    )

    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    try:
        stored_data = await store.async_load()
    except Exception:
        _LOGGER.exception("Critical error loading integration storage")
        stored_data = None

    try:
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

    # Make coordinator available immediately so any code invoked during first refresh can access it
    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}
    _LOGGER.debug("SolarACCoordinator stored in hass.data for entry %s (version=%s)", entry.entry_id, version)

    # Initial refresh (may trigger platform setup callbacks)
    await coordinator.async_config_entry_first_refresh()

    # Forward platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.add_update_listener(async_reload_entry)

    async def handle_reset_learning(call: ServiceCall):
        controller = getattr(coordinator, "controller", None)
        if controller:
            await controller.reset_learning()

    hass.services.async_register(DOMAIN, "reset_learning", handle_reset_learning)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    if not hass.data.get(DOMAIN):
        if hass.services.has_service(DOMAIN, "reset_learning"):
            hass.services.async_remove(DOMAIN, "reset_learning")
    return unload_ok
