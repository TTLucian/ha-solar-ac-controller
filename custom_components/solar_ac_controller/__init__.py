from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .coordinator import SolarACCoordinator
from .const import (
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
    CONF_INITIAL_LEARNED_POWER,
    CONF_ZONES,
    CONF_ZONE_TEMP_SENSORS,
    DEFAULT_INITIAL_LEARNED_POWER,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor"]
ALL_PLATFORMS = PLATFORMS + ["switch", "select"]


async def _async_migrate_data(
    old_major: int,
    old_minor: int,
    old_data: dict | None,
    initial_lp: float = DEFAULT_INITIAL_LEARNED_POWER,
) -> dict:
    """
    Normalize and migrate stored data for Solar AC Controller.
    STORAGE_VERSION is incremented whenever the structure of the stored payload changes.
    Document migration changes here and in commit messages for future maintainers.
    """

    if not isinstance(old_data, dict):
        return {"learned_power": {}, "samples": 0}

    learned_power = old_data.get("learned_power", {})
    if not isinstance(learned_power, dict):
        learned_power = {}
        return {"learned_power": {}, "samples": 0}
    modified = False
    for zone, val in list(learned_power.items()):
        if val is None:
            learned_power[zone] = {
                "default": initial_lp,
                "heat": initial_lp,
                "cool": initial_lp,
            }
            modified = True
        elif isinstance(val, (int, float)):
            v = float(val)
            learned_power[zone] = {"default": v, "heat": v, "cool": v}
            modified = True
        elif isinstance(val, dict):
            for mode in ["default", "heat", "cool"]:
                if mode not in val:
                    val[mode] = initial_lp
                    modified = True

    payload = {
        "learned_power": learned_power,
        "samples": old_data.get("samples", 0),
    }

    if modified:
        return payload
    return payload


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # Register services at setup for schema validation and best practices
    _svc_flag = "__svc_reset_learning_registered"
    if _svc_flag not in hass.data.setdefault(DOMAIN, {}):

        async def handle_reset_learning(call: ServiceCall):
            # Reset learning for all loaded coordinators
            for entry_dict in hass.data[DOMAIN].values():
                if not isinstance(entry_dict, dict):
                    continue
                coordinator = entry_dict.get("coordinator")
                controller = getattr(coordinator, "controller", None)
                if controller:
                    await controller.reset_learning()

        async def handle_force_relearn(call: ServiceCall):
            # Reset learned power for a specific zone or all zones, with validation
            zone = call.data.get("zone")
            zone_found = False
            for entry_dict in hass.data[DOMAIN].values():
                if not isinstance(entry_dict, dict):
                    continue
                coordinator = entry_dict.get("coordinator")
                controller = getattr(coordinator, "controller", None)
                if controller:
                    zones = set(getattr(coordinator, "config", {}).get(CONF_ZONES, []))
                    if zone:
                        if zone in zones:
                            await controller.reset_learning(zone)
                            zone_found = True
                    else:
                        await controller.reset_learning()
                        zone_found = True
            if zone and not zone_found:
                _LOGGER.warning(
                    f"force_relearn: Provided zone '{zone}' not found in any loaded Solar AC Controller instance."
                )

        hass.services.async_register(DOMAIN, "reset_learning", handle_reset_learning)
        hass.services.async_register(DOMAIN, "force_relearn", handle_force_relearn)
        hass.data[DOMAIN][_svc_flag] = True
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    # Migrate zone_temp_sensors from dict (old format) to list (new format)
    needs_update = False
    new_data = {**entry.data}
    new_options = {**entry.options}

    for data_dict in [new_data, new_options]:
        zone_temp_sensors = data_dict.get(CONF_ZONE_TEMP_SENSORS)
        if zone_temp_sensors and isinstance(zone_temp_sensors, dict):
            # Convert dict mapping to parallel list
            zones = data_dict.get(CONF_ZONES, [])
            zone_temp_sensors_list = []
            for zone_id in zones:
                zone_temp_sensors_list.append(zone_temp_sensors.get(zone_id, ""))
            data_dict[CONF_ZONE_TEMP_SENSORS] = zone_temp_sensors_list
            needs_update = True
            _LOGGER.info("Migrated zone_temp_sensors from dict to list format")

    if needs_update:
        hass.config_entries.async_update_entry(
            entry, data=new_data, options=new_options
        )

    # 1. Get Integration Version from manifest
    integration = await async_get_integration(hass, DOMAIN)
    version = str(integration.version) if integration.version else None

    initial_lp = entry.options.get(
        CONF_INITIAL_LEARNED_POWER,
        entry.data.get(CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER),
    )

    # 2. Storage Setup (manual migration because Store no longer accepts migrate_fn)
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)

    try:
        stored_data = await store.async_load()
    except Exception:  # pragma: no cover - defensive
        _LOGGER.exception("Failed to load stored data; falling back to defaults")
        stored_data = None

    if stored_data is None:
        stored_data = {"learned_power": {}, "samples": 0}

    # 1. Migrate
    migrated = await _async_migrate_data(0, 0, stored_data, initial_lp)
    if migrated != stored_data:
        stored_data = migrated

    # 2. Rounding cleanup
    def _round_map(val):
        if isinstance(val, dict):
            return {k: _round_map(v) for k, v in val.items()}
        return int(round(float(val)))

    stored_data["learned_power"] = _round_map(stored_data.get("learned_power", {}))

    # 3. Integration enabled state (persisted)
    # Use stored_data directly (store.data does not exist)
    stored_data["integration_enabled"] = stored_data.get("integration_enabled", True)

    # 4. Save ONCE
    try:
        await store.async_save(stored_data)
    except Exception:
        _LOGGER.debug("Skipped save during storage load")

    # 3. Create Device (The "Master" record)
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Solar AC Controller",
        sw_version=version,
        configuration_url="https://github.com/TTLucian/ha-solar-ac-controller",
    )

    # 5. Initialize Coordinator
    coordinator = SolarACCoordinator(
        hass,
        entry,
        store,
        stored_data,
        version=version,
    )

    # Integration enable/disable state (persisted)
    coordinator.integration_enabled = stored_data.get("integration_enabled", True)

    async def async_set_integration_enabled(enabled: bool):
        coordinator.integration_enabled = enabled
        # Persist state efficiently: update in-memory stored_data and save
        stored_data["integration_enabled"] = enabled
        await store.async_save(stored_data)
        coordinator.async_update_listeners()

    coordinator.async_set_integration_enabled = async_set_integration_enabled

    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, ALL_PLATFORMS)

    entry.add_update_listener(async_reload_entry)

    # Service registration moved to async_setup for best practices

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ALL_PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    if not hass.data.get(DOMAIN):
        if hass.services.has_service(DOMAIN, "reset_learning"):
            hass.services.async_remove(DOMAIN, "reset_learning")

    return unload_ok
