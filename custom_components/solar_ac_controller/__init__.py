from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    CONF_INITIAL_LEARNED_POWER,
    CONF_SEASON_MODE,
    CONF_ZONE_TEMP_SENSORS,
    CONF_ZONES,
    DEFAULT_INITIAL_LEARNED_POWER,
    DEFAULT_SEASON_MODE,
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
    SolarACData,
)
from .coordinator import SolarACCoordinator
from .exceptions import StorageError

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor"]
ALL_PLATFORMS = PLATFORMS + ["switch", "select", "number"]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


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

    Current logic assumes migration from any old version to current (handles all cases).
    For version-specific migrations, add conditional logic based on old_major/old_minor.
    """

    if not isinstance(old_data, dict):
        return {"learned_power": {}, "samples": 0}

    # Start with a copy of old_data to preserve non-migrated keys
    migrated_data = dict(old_data)

    learned_power = old_data.get("learned_power", {})
    if not isinstance(learned_power, dict):
        learned_power = {}
    for zone, val in learned_power.items():
        if val is None:
            learned_power[zone] = {
                "default": initial_lp,
                "heat": initial_lp,
                "cool": initial_lp,
            }
        elif isinstance(val, (int, float)):
            v = float(val)
            learned_power[zone] = {"default": v, "heat": v, "cool": v}
        elif isinstance(val, dict):
            for mode in ["default", "heat", "cool"]:
                if mode not in val:
                    val[mode] = initial_lp
        else:
            # Handle invalid types by setting to default
            _LOGGER.warning(
                f"Invalid learned_power value for zone {zone}: {val}, resetting to default"
            )
            learned_power[zone] = {
                "default": initial_lp,
                "heat": initial_lp,
                "cool": initial_lp,
            }

    migrated_data["learned_power"] = learned_power
    samples = old_data.get("samples", 0)
    if not isinstance(samples, (int, float)):
        samples = 0
    migrated_data["samples"] = int(samples)

    return migrated_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # Register services at setup for schema validation and best practices
    _svc_flag = "__svc_force_relearn_registered"
    domain_data: SolarACData = hass.data.setdefault(DOMAIN, {})
    if _svc_flag not in domain_data:

        async def handle_force_relearn(call: ServiceCall) -> None:
            # Reset learned power and samples for a specific zone or all zones
            zone_entity = call.data.get("zone")
            zone = zone_entity.split(".")[-1] if zone_entity else None

            # Validate zone if provided
            if zone:
                all_configured_zones = set()
                for entry_dict in domain_data.values():
                    if isinstance(entry_dict, dict):
                        coordinator = entry_dict.get("coordinator")
                        if coordinator:
                            zones = set(coordinator.config.get(CONF_ZONES, []))
                            all_configured_zones.update(zones)

                # Extract zone names (entity_id -> short name)
                zone_names = {z.split(".")[-1] for z in all_configured_zones}

                if zone not in zone_names:
                    raise ValueError(
                        f"Zone '{zone}' not found. Available zones: {', '.join(sorted(zone_names))}"
                    )

            for entry_dict in domain_data.values():
                if not isinstance(entry_dict, dict):
                    continue
                coordinator = entry_dict.get("coordinator")
                if not coordinator:
                    continue
                async with coordinator._storage_lock:
                    if zone:
                        # Reset learned power and samples for the specified zone only
                        if zone in coordinator.learned_power:
                            del coordinator.learned_power[zone]
                        if hasattr(coordinator, "samples"):
                            coordinator.samples = 0
                        persist_fn = getattr(
                            coordinator, "async_persist_learned_values", None
                        )
                        if persist_fn:
                            await persist_fn()
                        _LOGGER.info(
                            f"Force relearn: reset learned power and samples for zone {zone}"
                        )
                    else:
                        # Reset all learned power and samples
                        coordinator.learned_power = {}
                        if hasattr(coordinator, "samples"):
                            coordinator.samples = 0
                        persist_fn = getattr(
                            coordinator, "async_persist_learned_values", None
                        )
                        if persist_fn:
                            await persist_fn()
                        _LOGGER.info(
                            "Force relearn: reset all learned power and samples"
                        )

        hass.services.async_register(DOMAIN, "force_relearn", handle_force_relearn)
        domain_data[_svc_flag] = True
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
    store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)

    try:
        stored_data = await store.async_load()
    except (OSError, StorageError):  # pragma: no cover - defensive
        _LOGGER.exception("Failed to load stored data; falling back to defaults")
        stored_data = None

    if stored_data is None:
        stored_data = {"learned_power": {}, "samples": 0}

    # 1. Migrate
    migrated = await _async_migrate_data(0, 0, stored_data, initial_lp)
    if migrated != stored_data:
        stored_data = migrated

    # 2. Rounding cleanup
    def _round_map(val: Any) -> Any:
        if isinstance(val, dict):
            return {k: _round_map(v) for k, v in val.items()}
        if isinstance(val, (int, float)):
            return int(round(float(val)))
        return val

    stored_data["learned_power"] = _round_map(stored_data.get("learned_power", {}))

    # 3. Integration enabled state (persisted)
    # Use stored_data directly (store.data does not exist)
    stored_data["integration_enabled"] = stored_data.get("integration_enabled", True)
    # 3b. Activity logging enabled state (persisted)
    stored_data["activity_logging_enabled"] = stored_data.get(
        "activity_logging_enabled", False
    )
    # 3c. Season mode state (persisted)
    stored_data["season_mode"] = stored_data.get(
        "season_mode",
        entry.options.get(
            CONF_SEASON_MODE, entry.data.get(CONF_SEASON_MODE, DEFAULT_SEASON_MODE)
        ),
    )

    # 4. Save ONCE
    try:
        await store.async_save(stored_data)
    except (OSError, StorageError):
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
    coordinator.activity_logging_enabled = stored_data.get(
        "activity_logging_enabled", False
    )

    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, ALL_PLATFORMS)

    entry.add_update_listener(async_reload_entry)

    # Service registration moved to async_setup for best practices

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate config entry from version 1 to 2."""
    _LOGGER.debug(
        "Migrating configuration from version %s.%s",
        config_entry.version,
        config_entry.minor_version,
    )

    if config_entry.version > 2:
        # This means the user has downgraded from a future version
        return False

    if config_entry.version == 1:
        # Simply update the version without changing data
        # The unified system will use defaults if old keys don't exist
        hass.config_entries.async_update_entry(config_entry, version=2)
        _LOGGER.debug(
            "Migration to configuration version %s.%s successful",
            config_entry.version,
            config_entry.minor_version,
        )
        return True

    return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ALL_PLATFORMS)

    if unload_ok:
        # Clean up coordinator tasks before removing
        domain_data: SolarACData = hass.data.get(DOMAIN, {})
        entry_data = domain_data.get(entry.entry_id)
        if entry_data and isinstance(entry_data, dict):
            coordinator = entry_data.get("coordinator")
            if coordinator:
                await coordinator._async_cleanup_tasks()

        # Remove the specific instance data
        domain_data.pop(entry.entry_id, None)

    # If this was the last instance, clean up the global services
    # We check if DOMAIN is in hass.data and if it has any keys other than the service flag
    remaining_entries = [
        k for k in hass.data.get(DOMAIN, {}) if k != "__svc_force_relearn_registered"
    ]

    if not remaining_entries:
        for service in ["force_relearn"]:
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)
        # Optional: remove the service flag too
        hass.data[DOMAIN].pop("__svc_force_relearn_registered", None)

    return bool(unload_ok)
