import asyncio
import logging

import pytest

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator


class FakeHass:
    def async_create_task(self, coro):
        return asyncio.create_task(coro)


class MockStore:
    def __init__(self):
        self.saved = None

    async def async_save(self, data):
        self.saved = data


@pytest.mark.asyncio
async def test_async_persist_learned_values_saves_rounded_values():
    """async_persist_learned_values rounds floats to whole watts before persisting."""
    coord = object.__new__(SolarACCoordinator)
    coord._storage_lock = asyncio.Lock()
    coord.stored_data = {}
    coord._storage_dirty = False
    coord.hass = FakeHass()
    coord._storage_debounce_task = None
    coord._last_storage_save = 0.0
    coord._storage_debounce_seconds = 5.0

    coord.learned_power = {
        "zone1": {"default": 123.6, "heat": 200.9, "cool": 150.0, "lead_delta": 0.0},
        "zone2": {"default": 45.2, "heat": 45.2, "cool": 45.2, "lead_delta": 0.0},
    }
    coord.samples = 7
    coord.learned_idle_power = 18.75
    coord.idle_power_samples = 10
    coord.zone_action_history = {}

    mock_store = MockStore()
    coord.store = mock_store

    await coord.async_persist_learned_values()

    assert isinstance(mock_store.saved, dict)
    assert mock_store.saved["learned_power"]["zone1"]["default"] == 124
    assert mock_store.saved["learned_power"]["zone1"]["heat"] == 201
    assert mock_store.saved["learned_power"]["zone2"]["default"] == 45
    assert mock_store.saved["samples"] == 7
    assert mock_store.saved["idle_power"] == 18.8
    assert mock_store.saved["idle_power_samples"] == 10
    assert coord._storage_dirty is False


def test__rounded_power_nested_and_non_numeric():
    coord = object.__new__(SolarACCoordinator)
    value = {"a": {"x": 12.7, "y": "n/a"}, "b": 3.2}
    out = SolarACCoordinator._rounded_power(coord, value)
    assert out["a"]["x"] == 13
    assert out["a"]["y"] == "n/a"
    assert out["b"] == 3


@pytest.mark.asyncio
async def test_create_task_ignored_cancel_does_not_log(caplog):
    caplog.set_level(logging.ERROR)
    coord = object.__new__(SolarACCoordinator)
    coord.hass = FakeHass()

    task = coord.create_task(asyncio.sleep(1))
    await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert "Background task exception" not in caplog.text
