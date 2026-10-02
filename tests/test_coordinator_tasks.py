import asyncio
import logging
from collections.abc import Callable
from typing import Any, cast

import pytest

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator


class FakeHass:
    class MockLoop:
        def call_later(self, delay: float, callback: Callable[..., Any], *args: Any) -> None:
            callback(*args)
            return None

    def __init__(self) -> None:
        self.loop = self.MockLoop()

    def async_create_task(self, coro: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)


@pytest.mark.asyncio
async def test_create_task_logs_exception(caplog: pytest.LogCaptureFixture) -> None:
    """create_task attaches a done-callback that logs unhandled exceptions."""
    caplog.set_level(logging.ERROR)
    coord = object.__new__(SolarACCoordinator)
    coord.hass = cast(Any, FakeHass())

    async def _boom() -> None:
        raise RuntimeError("boom")

    task = coord.create_task(_boom())
    await asyncio.sleep(0.01)
    try:
        await asyncio.wait_for(task, timeout=1)
    except RuntimeError:
        pass

    assert "Background task exception" in caplog.text


@pytest.mark.asyncio
async def test_async_set_integration_enabled_updates_stored_data() -> None:
    """Switching integration enabled updates in-memory state and schedules a save."""
    coord = object.__new__(SolarACCoordinator)
    coord.integration_enabled = False
    coord.stored_data = {}
    coord._storage_lock = asyncio.Lock()
    coord._storage_dirty = False

    async def fake_log(message: str, level: str | None = "info") -> None:
        pass

    save_called = {"count": 0}

    async def fake_debounced_save() -> None:
        save_called["count"] += 1

    coord._log = fake_log  # type: ignore[method-assign]
    coord._debounced_save = fake_debounced_save  # type: ignore[method-assign]
    coord._debounce_recalc = lambda: None  # type: ignore[method-assign]

    await coord.async_set_integration_enabled(True)

    assert coord.integration_enabled is True
    assert coord.stored_data["integration_enabled"] is True
    assert coord._storage_dirty is True
    assert save_called["count"] == 1
