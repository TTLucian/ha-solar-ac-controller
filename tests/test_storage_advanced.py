import asyncio
import logging

import pytest
from homeassistant.util import dt as dt_util

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
async def test_concurrent_debounced_saves_end_with_latest_value():
    """Multiple rapid _debounced_save calls collapse into a single write of the latest value."""
    coord = object.__new__(SolarACCoordinator)
    coord.hass = FakeHass()
    coord._storage_lock = asyncio.Lock()
    coord._storage_debounce_task = None
    coord._last_storage_save = dt_util.utcnow().timestamp()
    coord._storage_debounce_seconds = 0.12
    coord._storage_dirty = True

    store = MockStore()
    coord.store = store

    async def worker(val, delay):
        await asyncio.sleep(delay)
        coord.stored_data = {"v": val}
        coord._storage_dirty = True
        await coord._debounced_save()

    tasks = [asyncio.create_task(worker(i, i * 0.02)) for i in range(5)]
    await asyncio.gather(*tasks)
    await asyncio.sleep(0.5)

    assert store.saved == {"v": 4}


@pytest.mark.asyncio
async def test_flush_pending_storage_save_cancels_and_saves_immediately():
    """_flush_pending_storage_save cancels the debounce timer and saves immediately."""
    coord = object.__new__(SolarACCoordinator)
    coord.hass = FakeHass()
    coord._storage_lock = asyncio.Lock()
    coord._storage_debounce_task = None
    coord._last_storage_save = dt_util.utcnow().timestamp()
    coord._storage_debounce_seconds = 1.0  # long debounce so flush is meaningful
    coord._storage_dirty = True

    class SlowStore:
        def __init__(self):
            self.saved = None

        async def async_save(self, data):
            await asyncio.sleep(0.05)
            self.saved = data

    store = SlowStore()
    coord.store = store

    coord.stored_data = {"x": 1}
    coord._storage_dirty = True
    await coord._debounced_save()  # schedules a delayed save (1 s from now)

    coord.stored_data = {"x": 2}
    coord._storage_dirty = True
    await coord._flush_pending_storage_save()  # should cancel and save immediately

    await asyncio.sleep(0.1)
    assert store.saved == {"x": 2}


@pytest.mark.asyncio
async def test_perform_storage_save_logs_oserror_and_keeps_dirty(caplog):
    """An OSError during save is logged; _storage_dirty stays True so the next cycle retries."""
    caplog.set_level(logging.ERROR)
    coord = object.__new__(SolarACCoordinator)
    coord.stored_data = {"k": 1}
    coord._storage_dirty = True

    class FailingStore:
        async def async_save(self, data):
            raise OSError("disk full")

    coord.store = FailingStore()
    await coord._perform_storage_save()

    assert coord._storage_dirty is True
    assert any("Error saving to storage" in r.message for r in caplog.records)
