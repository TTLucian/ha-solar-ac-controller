import asyncio
from types import SimpleNamespace
from typing import cast

import pytest

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator
from custom_components.solar_ac_controller.storage_circuit_breaker import (
    StorageCircuitBreaker,
)


class MockStore:
    def __init__(self, event: asyncio.Event):
        self._event = event
        self.saved = None

    async def async_save(self, data):
        # Record the received object reference/value then wait to simulate slow I/O
        self.saved = data
        await self._event.wait()


@pytest.mark.asyncio
async def test_perform_storage_save_deepcopy():
    # Arrange: minimal coordinator object (bypass __init__)
    coord = object.__new__(SolarACCoordinator)
    # Provide a storage lock used by the implementation
    coord._storage_lock = asyncio.Lock()

    # Circuit breaker that allows operation and records success/failure (no-op)
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

    # Prepare mocked store that blocks until event is set
    event = asyncio.Event()
    mock_store = MockStore(event)
    coord.store = mock_store

    # Seed stored_data with a nested structure and mark dirty
    coord.stored_data = {"zone": {"power": 100}}
    coord._storage_dirty = True

    # Act: start the save task which will copy the data then block on event
    save_task = asyncio.create_task(coord._perform_storage_save())

    # Wait briefly to allow _perform_storage_save to call into store.async_save
    await asyncio.sleep(0.01)

    # Mutate the in-memory stored_data while save is pending
    coord.stored_data["zone"]["power"] = 999

    # Unblock the mocked store save so it completes
    event.set()
    await save_task

    # Assert: the data passed to store was the original snapshot (deep-copied)
    assert mock_store.saved == {"zone": {"power": 100}}
    # And the coordinator marked storage as not dirty after successful save
    assert coord._storage_dirty is False
