import asyncio

import pytest

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator


@pytest.mark.asyncio
async def test_async_set_activity_logging_enabled_updates_state_and_saves():
    """Enabling activity logging updates in-memory state, stored_data, and schedules a save."""
    coord = object.__new__(SolarACCoordinator)
    coord.activity_logging_enabled = False
    coord.stored_data = {}
    coord._storage_lock = asyncio.Lock()
    coord._storage_dirty = False

    save_called = {"count": 0}

    async def fake_log(message: str, level: str | None = "info") -> None:
        pass

    async def fake_debounced_save():
        save_called["count"] += 1

    coord._log = fake_log  # type: ignore[method-assign]
    coord._debounced_save = fake_debounced_save
    coord._debounce_recalc = lambda: None

    await coord.async_set_activity_logging_enabled(True)

    assert coord.activity_logging_enabled is True
    assert coord.stored_data["activity_logging_enabled"] is True
    assert coord._storage_dirty is True
    assert save_called["count"] == 1


@pytest.mark.asyncio
async def test_async_set_activity_logging_disabled_updates_state_and_saves():
    """Disabling activity logging updates state and schedules a save."""
    coord = object.__new__(SolarACCoordinator)
    coord.activity_logging_enabled = True
    coord.stored_data = {"activity_logging_enabled": True}
    coord._storage_lock = asyncio.Lock()
    coord._storage_dirty = False

    save_called = {"count": 0}

    async def fake_log(message: str, level: str | None = "info") -> None:
        pass

    async def fake_debounced_save():
        save_called["count"] += 1

    coord._log = fake_log  # type: ignore[method-assign]
    coord._debounced_save = fake_debounced_save
    coord._debounce_recalc = lambda: None

    await coord.async_set_activity_logging_enabled(False)

    assert coord.activity_logging_enabled is False
    assert coord.stored_data["activity_logging_enabled"] is False
    assert save_called["count"] == 1
