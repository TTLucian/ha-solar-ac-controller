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


async def fake_log(message: str, level: str | None = "info") -> None:
    """No-op activity logger for coordinator tests."""


async def _noop() -> None:
    """No-op freeze cleanup for coordinator tests."""


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
async def test_async_set_integration_suspended_updates_stored_data() -> None:
    """Suspending updates in-memory state and schedules a save."""
    coord = object.__new__(SolarACCoordinator)
    coord.integration_suspended = False
    coord.integration_disabled = False
    coord.suspend_armed = False
    coord.integration_active = True
    coord.last_action = None
    coord._state_lock = asyncio.Lock()
    coord.stored_data = {}
    coord._storage_lock = asyncio.Lock()
    coord._storage_dirty = False
    coord.panic_manager = None  # type: ignore[assignment]
    coord._perform_freeze_cleanup = _noop  # type: ignore[method-assign]

    save_called = {"count": 0}

    async def fake_debounced_save() -> None:
        save_called["count"] += 1

    coord._log = fake_log  # type: ignore[method-assign]
    coord._debounced_save = fake_debounced_save  # type: ignore[method-assign]
    coord._debounce_recalc = lambda: None  # type: ignore[method-assign]

    await coord.async_set_integration_suspended(True)

    assert coord.integration_suspended is True
    assert coord.integration_enabled is False
    assert coord.stored_data["integration_suspended"] is True
    assert coord.stored_data["suspend_armed"] is False
    assert coord._storage_dirty is True
    assert save_called["count"] == 1


@pytest.mark.asyncio
async def test_async_set_integration_disabled_updates_stored_data() -> None:
    """Disabling updates in-memory state and schedules a save."""
    coord = object.__new__(SolarACCoordinator)
    coord.integration_suspended = False
    coord.integration_disabled = False
    coord.suspend_armed = False
    coord.integration_active = True
    coord.last_action = None
    coord._state_lock = asyncio.Lock()
    coord.stored_data = {}
    coord._storage_lock = asyncio.Lock()
    coord._storage_dirty = False
    coord.panic_manager = None  # type: ignore[assignment]
    coord._perform_freeze_cleanup = _noop  # type: ignore[method-assign]

    save_called = {"count": 0}

    async def fake_debounced_save() -> None:
        save_called["count"] += 1

    coord._log = fake_log  # type: ignore[method-assign]
    coord._debounced_save = fake_debounced_save  # type: ignore[method-assign]
    coord._debounce_recalc = lambda: None  # type: ignore[method-assign]

    await coord.async_set_integration_disabled(True)

    assert coord.integration_disabled is True
    assert coord.stored_data["integration_disabled"] is True
    assert save_called["count"] == 1
