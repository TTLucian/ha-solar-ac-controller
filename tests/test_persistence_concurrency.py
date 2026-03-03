import asyncio

import pytest

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator


class MockStore:
    def __init__(self, event: asyncio.Event):
        self._event = event
        self.saved = None

    async def async_save(self, data):
        self.saved = data
        await self._event.wait()


@pytest.mark.asyncio
async def test_perform_storage_save_deepcopy():
    """Mutations to stored_data during a slow I/O write don't corrupt the saved snapshot."""
    coord = object.__new__(SolarACCoordinator)
    coord._storage_dirty = True
    coord.stored_data = {"zone": {"power": 100}}

    event = asyncio.Event()
    mock_store = MockStore(event)
    coord.store = mock_store

    save_task = asyncio.create_task(coord._perform_storage_save())
    # Give _perform_storage_save time to deep-copy and enter async_save
    await asyncio.sleep(0.01)

    # Mutate in-memory state while the I/O call is still blocked
    coord.stored_data["zone"]["power"] = 999

    event.set()
    await save_task

    # The store must have received the original snapshot, not the mutation
    assert mock_store.saved == {"zone": {"power": 100}}
    assert coord._storage_dirty is False
