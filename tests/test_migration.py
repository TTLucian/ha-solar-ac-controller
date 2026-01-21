import pytest

from custom_components.solar_ac_controller.__init__ import (
    DEFAULT_INITIAL_LEARNED_POWER,
    _async_migrate_data,
)


@pytest.mark.asyncio
async def test_migrate_none_old_data():
    assert await _async_migrate_data(0, 0, None, DEFAULT_INITIAL_LEARNED_POWER) == {
        "learned_power": {},
        "samples": 0,
    }


@pytest.mark.asyncio
async def test_migrate_numeric_and_none_entries():
    old = {"learned_power": {"zone.a": None, "zone.b": 1234}, "samples": 5}
    out = await _async_migrate_data(0, 0, old, 1000.0)
    assert out["samples"] == 5
    assert out["learned_power"]["zone.a"]["default"] == 1000.0
    assert out["learned_power"]["zone.b"]["cool"] == 1234.0


@pytest.mark.asyncio
async def test_migrate_dict_adds_modes():
    old = {"learned_power": {"z": {"default": 1500}}, "samples": 0}
    out = await _async_migrate_data(0, 0, old, 1000.0)
    assert out["learned_power"]["z"]["heat"] == 1000.0
    assert out["learned_power"]["z"]["cool"] == 1000.0
