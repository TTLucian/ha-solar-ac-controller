from typing import Any, cast

import pytest
from homeassistant.core import HomeAssistant

from custom_components.solar_ac_controller import (
    DEFAULT_INITIAL_LEARNED_POWER,
    _async_migrate_data,
)
from custom_components.solar_ac_controller.coordinator import SolarACCoordinator


@pytest.mark.asyncio
async def test_migrate_none_old_data() -> None:
    assert await _async_migrate_data(0, 0, None, DEFAULT_INITIAL_LEARNED_POWER) == {
        "learned_power": {},
        "samples": 0,
    }


@pytest.mark.asyncio
async def test_migrate_numeric_and_none_entries() -> None:
    old = {"learned_power": {"zone.a": None, "zone.b": 1234}, "samples": 5}
    out = await _async_migrate_data(0, 0, old, 1000.0)
    assert out["samples"] == 5
    assert out["learned_power"]["zone.a"]["default"] == 1000.0
    assert out["learned_power"]["zone.b"]["cool"] == 1234.0


@pytest.mark.asyncio
async def test_migrate_dict_adds_modes() -> None:
    old = {"learned_power": {"z": {"default": 1500}}, "samples": 0}
    out = await _async_migrate_data(0, 0, old, 1000.0)
    assert out["learned_power"]["z"]["heat"] == 1000.0
    assert out["learned_power"]["z"]["cool"] == 1000.0


@pytest.mark.asyncio
async def test_migrate_preserves_other_keys() -> None:
    old = {
        "learned_power": {"zone.a": 1000},
        "samples": 5,
        "integration_enabled": True,
        "activity_logging_enabled": True,
        "custom_key": "value",
    }
    out = await _async_migrate_data(0, 0, old, 1000.0)
    assert out["samples"] == 5
    assert out["integration_enabled"] is True
    assert out["activity_logging_enabled"] is True
    assert out["custom_key"] == "value"
    assert out["learned_power"]["zone.a"]["default"] == 1000.0


def test_init_learned_data_survives_missing_mode_keys() -> None:
    """Stored zone entries missing default/heat/cool must not crash setup.

    _init_learned_data() backfills absent mode keys from
    self.initial_learned_power. That attribute is created by
    _init_config_values(), which must therefore run BEFORE
    _init_learned_data() in __init__. If the order regresses this raises
    AttributeError and the whole config entry fails to load.
    """
    coord = object.__new__(SolarACCoordinator)
    coord.initial_learned_power = 1000.0

    # 'cool' is missing entirely; only heat was ever learned.
    coord._init_learned_data({"learned_power": {"zone1": {"heat": 1200.0}}})

    entry = coord.learned_power["zone1"]
    assert entry.get("heat") == 1200.0
    # Missing modes are backfilled from initial_learned_power, not zeroed.
    assert entry.get("default") == 1000.0
    assert entry.get("cool") == 1000.0


def test_init_learned_data_survives_malformed_entries() -> None:
    """Non-dict and garbage stored entries must fall back, not raise."""
    coord = object.__new__(SolarACCoordinator)
    coord.initial_learned_power = 1000.0

    coord._init_learned_data({"learned_power": {"bad": 1234.0, "worse": None, "empty": {}}})

    for zone in ("bad", "worse", "empty"):
        entry = coord.learned_power[zone]
        assert entry.get("default") == 1000.0
        assert entry.get("heat") == 1000.0
        assert entry.get("cool") == 1000.0


def test_runtime_state_does_not_clobber_persisted_values() -> None:
    """_init_runtime_state must not overwrite values loaded from storage.

    Idle power, its sample count and the zone action history are all persisted.
    They are owned by _init_learned_data(); re-declaring them in
    _init_runtime_state() wiped them on every restart, so the idle baseline
    had to re-learn from scratch and the action history was always empty.
    """
    coord = object.__new__(SolarACCoordinator)
    coord.hass = cast(Any, None)
    coord.store = cast(Any, None)
    coord.initial_learned_power = 1000.0

    stored = {
        "learned_power": {"zone1": {"default": 1200.0, "heat": 1200.0, "cool": 900.0}},
        "samples": 7,
        "idle_power": 42.5,
        "idle_power_samples": 88,
        "zone_action_history": {"zone1": [{"action": "on"}]},
    }
    coord._init_learned_data(stored)
    coord._init_runtime_state()

    assert coord.learned_idle_power == pytest.approx(42.5)
    assert coord.idle_power_samples == 88
    assert coord.zone_action_history == {"zone1": [{"action": "on"}]}
    # learned_power / samples were never affected, but assert for completeness
    assert coord.samples == 7
    assert coord.learned_power["zone1"].get("cool") == 900.0


def test_config_entry_version_matches_migration_target() -> None:
    """ConfigFlow.VERSION must be >= the version async_migrate_entry writes.

    HA refuses to load a config entry whose ``version`` is higher than the
    flow handler's ``VERSION`` (it logs an error and returns False before
    ``async_migrate_entry`` is ever consulted).  A mismatch here therefore
    permanently breaks the entry on the restart after migration.
    """
    from custom_components.solar_ac_controller.config_flow import ConfigFlow

    # async_migrate_entry writes version=2 for entries arriving at version 1.
    MIGRATION_TARGET_VERSION = 2

    assert ConfigFlow.VERSION >= MIGRATION_TARGET_VERSION, (
        f"ConfigFlow.VERSION ({ConfigFlow.VERSION}) is lower than the version "
        f"written by async_migrate_entry ({MIGRATION_TARGET_VERSION}); HA will "
        "refuse to load this config entry after the first restart"
    )


@pytest.mark.asyncio
async def test_async_migrate_entry_upgrades_v1_entry() -> None:
    """A v1 entry must be migrated to v2 without altering its payload."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from custom_components.solar_ac_controller import async_migrate_entry

    entry = SimpleNamespace(version=1, minor_version=1)
    updated: dict = {}

    def _update(_entry: Any, **kwargs: Any) -> bool:
        updated.update(kwargs)
        _entry.version = kwargs.get("version", _entry.version)
        return True

    hass = cast(
        HomeAssistant,
        SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=MagicMock(side_effect=_update))),
    )

    assert await async_migrate_entry(hass, cast(Any, entry)) is True
    assert updated.get("version") == 2
    assert entry.version == 2


@pytest.mark.asyncio
async def test_async_migrate_entry_rejects_future_version() -> None:
    """An entry newer than the handler must be rejected, not silently accepted."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from custom_components.solar_ac_controller import async_migrate_entry
    from custom_components.solar_ac_controller.config_flow import ConfigFlow

    entry = SimpleNamespace(version=ConfigFlow.VERSION + 1, minor_version=1)
    hass = cast(
        HomeAssistant,
        SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=MagicMock())),
    )

    assert await async_migrate_entry(hass, cast(Any, entry)) is False
