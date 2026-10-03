"""A dropped sensor state write must be visible.

`_sync_write_ha_state` scheduled the async write through three different task
creators and ended in `except Exception: pass`. Two things could therefore go
wrong with nothing recorded anywhere:

* every creator refusing the write - the sensor simply stopped updating, with
  no log entry to explain why;
* `coordinator.create_background_task` returning `None`, which it does when it
  cannot create the task. The return value was discarded, so the coroutine was
  built and then dropped un-awaited: the write was lost *and* the interpreter
  emitted a "coroutine was never awaited" warning.

The module had no logger at all, which is almost certainly why the original
author reached for `pass`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from custom_components.solar_ac_controller.sensor import _BaseSolarACSensor


class _ExplodingCreator:
    """Task creator that refuses every coroutine."""

    def create_background_task(self, coro: Any) -> None:
        raise RuntimeError("no loop")


class _NoneReturningCreator:
    """Mirrors the coordinator: returns None instead of raising."""

    def create_background_task(self, coro: Any) -> None:
        return None


class _WorkingCreator:
    def __init__(self) -> None:
        self.scheduled: list[Any] = []

    def create_background_task(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.get_event_loop().create_task(coro)
        self.scheduled.append(task)
        return task


def _sensor(coordinator: Any | None, hass: Any | None = object()) -> Any:
    """A bare entity carrying only what _sync_write_ha_state touches."""
    sensor = object.__new__(_BaseSolarACSensor)
    sensor.hass = hass  # type: ignore[assignment]
    sensor.coordinator = coordinator
    sensor.entity_id = "sensor.test_write"

    async def _write() -> None:
        return None

    sensor._smart_write_ha_state = _write  # type: ignore[method-assign]
    return sensor


@pytest.mark.asyncio
async def test_none_from_create_background_task_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """The coordinator returning None means the write is lost; say so."""
    sensor = _sensor(_NoneReturningCreator())

    with caplog.at_level(logging.WARNING):
        sensor._sync_write_ha_state()
    await asyncio.sleep(0)  # let any stray coroutine report

    assert any(r.levelno >= logging.WARNING for r in caplog.records), (
        "create_background_task returned None, so the state write was dropped without anything being logged"
    )


@pytest.mark.asyncio
async def test_all_creators_failing_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """If no creator will take the write, that must be logged, not swallowed."""

    class _BadHass:
        def async_create_task(self, coro: Any) -> None:
            coro.close()
            raise RuntimeError("loop is closed")

    class _BadCoordinator:
        def create_background_task(self, coro: Any) -> None:
            coro.close()
            raise RuntimeError("no loop")

        def create_task(self, coro: Any) -> None:
            coro.close()
            raise RuntimeError("no loop")

    sensor = _sensor(_BadCoordinator(), hass=_BadHass())

    with caplog.at_level(logging.WARNING):
        sensor._sync_write_ha_state()

    assert caplog.records, "every task creator refused the write and nothing was logged"


@pytest.mark.asyncio
async def test_orphaned_coroutine_is_closed(caplog: pytest.LogCaptureFixture) -> None:
    """A coroutine built but never scheduled must not be left un-awaited.

    The original fallback built a second coroutine and dropped the first, so
    Python would later warn that the first was never awaited. Every coroutine
    handed to a creator that refuses it should be closed.
    """
    built: list[Any] = []

    def _make() -> Any:
        coro = asyncio.sleep(0)
        built.append(coro)
        return coro

    class _BadCoordinator:
        def create_background_task(self, coro: Any) -> None:
            coro.close()
            raise RuntimeError("no loop")

        def create_task(self, coro: Any) -> None:
            coro.close()
            raise RuntimeError("no loop")

    class _BadHass:
        def async_create_task(self, coro: Any) -> None:
            coro.close()
            raise RuntimeError("loop is closed")

    sensor = _sensor(_BadCoordinator(), hass=_BadHass())
    sensor._smart_write_ha_state = _make

    with caplog.at_level(logging.WARNING):
        sensor._sync_write_ha_state()

    assert built, "the test did not exercise the write path"
    # A coroutine that has been started or closed has cr_frame None.
    unclosed = [c for c in built if c.cr_frame is not None]
    assert not unclosed, f"{len(unclosed)} coroutine(s) were built and never awaited"


@pytest.mark.asyncio
async def test_successful_schedule_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    """The happy path stays quiet - no warning noise on every state change."""
    creator = _WorkingCreator()
    sensor = _sensor(creator)

    with caplog.at_level(logging.WARNING):
        sensor._sync_write_ha_state()
    await asyncio.gather(*creator.scheduled)

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], "a successful state write logged a warning"
