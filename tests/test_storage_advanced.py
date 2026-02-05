import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from homeassistant.util import dt as dt_util

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator
from custom_components.solar_ac_controller.storage_circuit_breaker import (
    StorageCircuitBreaker,
)


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
    coord = object.__new__(SolarACCoordinator)
    coord.hass = FakeHass()
    coord._storage_lock = asyncio.Lock()
    coord._storage_debounce_task = None
    coord._last_storage_save = dt_util.utcnow().timestamp()
    coord._storage_debounce_seconds = 0.12
    coord._storage_dirty = True

    coord.storage_circuit_breaker = cast(
        StorageCircuitBreaker,
        SimpleNamespace(
            should_attempt_operation=lambda: asyncio.sleep(0, result=True),
            record_success=lambda: asyncio.sleep(0, result=None),
            record_failure=lambda: asyncio.sleep(0, result=None),
        ),
    )

    store = MockStore()
    coord.store = store

    async def worker(val, delay):
        await asyncio.sleep(delay)
        coord.stored_data = {"v": val}
        coord._storage_dirty = True
        await coord._debounced_save()

    # Schedule several workers that call _debounced_save at staggered times
    tasks = [asyncio.create_task(worker(i, i * 0.02)) for i in range(5)]
    await asyncio.gather(*tasks)

    # Wait long enough for final debounced save to execute
    await asyncio.sleep(0.5)

    assert store.saved == {"v": 4}


@pytest.mark.asyncio
async def test_flush_pending_storage_save_cancels_and_saves_immediately():
    coord = object.__new__(SolarACCoordinator)
    coord.hass = FakeHass()
    coord._storage_lock = asyncio.Lock()
    coord._storage_debounce_task = None
    coord._last_storage_save = dt_util.utcnow().timestamp()
    coord._storage_debounce_seconds = 1.0  # long debounce to allow scheduling
    coord._storage_dirty = True

    async def _should_attempt():
        return True

    async def _record_success():
        return None

    async def _record_failure():
        return None

    coord.storage_circuit_breaker = cast(
        StorageCircuitBreaker,
        SimpleNamespace(
            should_attempt_operation=_should_attempt,
            record_success=_record_success,
            record_failure=_record_failure,
        ),
    )

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
    # Schedule a debounced delayed save
    await coord._debounced_save()

    # Now mutate and flush pending save which should cancel delayed task and save immediately
    coord.stored_data = {"x": 2}
    coord._storage_dirty = True
    await coord._flush_pending_storage_save()

    # Wait a bit for immediate save to complete
    await asyncio.sleep(0.1)

    assert store.saved == {"x": 2}


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_recovery_with_real_saves():
    fake_log = SimpleNamespace(messages=[])

    async def log_fn(message, level="info"):
        fake_log.messages.append((level, message))

    fake_log._log = log_fn

    cb = StorageCircuitBreaker(max_failures=2, reset_timeout=1, coordinator=fake_log)

    coord = object.__new__(SolarACCoordinator)
    coord.hass = FakeHass()
    coord._storage_lock = asyncio.Lock()
    coord.stored_data = {"k": 1}
    coord._storage_dirty = True
    coord.storage_circuit_breaker = cb

    class FlakyStore:
        def __init__(self):
            self.calls = 0

        async def async_save(self, data):
            self.calls += 1
            if self.calls <= 2:
                raise OSError("io error")
            return None

    store = FlakyStore()
    coord.store = store

    # Two failures to open the circuit
    await coord._perform_storage_save()
    await coord._perform_storage_save()

    assert cb.failure_count >= 2
    assert await cb.should_attempt_operation() is False

    # Wait for reset timeout to allow half-open
    await asyncio.sleep(1.1)

    # Now attempt save again; FlakyStore will succeed on third call
    await coord._perform_storage_save()

    # Circuit breaker should have reset (failure_count 0)
    assert cb.failure_count == 0
    # And we should have seen log entries indicating state changes
    assert any(
        "disabled" in m[1]
        or "entering recovery" in m[1]
        or "re-enabled" in m[1]
        or "recovered" in m[1]
        for m in fake_log.messages
    )
