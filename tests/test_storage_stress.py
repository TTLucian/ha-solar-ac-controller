import asyncio

import pytest
from homeassistant.util import dt as dt_util

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator


class FakeHass:
    def async_create_task(self, coro):
        return asyncio.create_task(coro)


class SlowStore:
    def __init__(self, delay=0.01):
        self.saved = None
        self.delay = delay

    async def async_save(self, data):
        await asyncio.sleep(self.delay)
        self.saved = data


@pytest.mark.asyncio
async def test_stress_concurrent_debounced_saves_saves_latest():
    """40 concurrent update+save calls must result in the last value being written."""
    coord = object.__new__(SolarACCoordinator)
    coord.hass = FakeHass()
    coord._storage_lock = asyncio.Lock()
    coord._storage_debounce_task = None
    coord._last_storage_save = dt_util.utcnow().timestamp()
    coord._storage_debounce_seconds = 0.08
    coord._storage_dirty = True

    store = SlowStore(delay=0.01)
    coord.store = store

    async def updater(i):
        await asyncio.sleep(i * 0.005)
        coord.stored_data = {"n": i}
        coord._storage_dirty = True
        await coord._debounced_save()

    tasks = [asyncio.create_task(updater(i)) for i in range(40)]
    await asyncio.gather(*tasks)
    await asyncio.sleep(1.0)

    assert store.saved == {"n": 39}


@pytest.mark.asyncio
async def test_intermittent_failures_do_not_lose_last_successful_save():
    """Intermittent OSErrors leave dirty=True; successful saves clear it with the right data."""
    coord = object.__new__(SolarACCoordinator)
    coord._storage_dirty = True

    class FlakyStore:
        def __init__(self):
            self.saved = None
            self.calls = 0

        async def async_save(self, data):
            self.calls += 1
            # Every 3rd call succeeds; the rest raise OSError
            if self.calls % 3 != 0:
                raise OSError("intermittent failure")
            self.saved = data

    store = FlakyStore()
    coord.store = store

    # 9 calls: calls 3, 6, 9 succeed (i=2, 5, 8)
    for i in range(9):
        coord.stored_data = {"v": i}
        coord._storage_dirty = True
        await coord._perform_storage_save()

    # Last successful save is call 9 which writes i=8
    assert store.saved == {"v": 8}
    assert coord._storage_dirty is False
