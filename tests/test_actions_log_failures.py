"""A logging failure must never discard state or fail a completed action.

`remove_zone` used to wrap the compressor recovery window in a
`try/except Exception: pass` whose comment said "do not break zone removal on
logging failures". The state write was inside that guard, so any failure there
- a bad ramp value, a float overflow, an AttributeError - was discarded
identically to a log failure and left no trace. That window is what stops a
zone being re-added while the compressor ramps; silently not setting it means
short cycling.

Separately, the trailing `_log` on every action path sat AFTER the zone had
been switched and short-cycle tracking updated. A failure there propagated into
`_async_update_data`, which marks the cycle failed and reports an operation
that actually worked as broken - the same shape as the compressor-relay bug.

`CancelledError` must still propagate: it is a BaseException and signals the
event loop that the coroutine should terminate.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from custom_components.solar_ac_controller.actions import ActionExecutor
from custom_components.solar_ac_controller.coordinator import SolarACCoordinator


class _Session:
    async def notify_zone_changed_during_learning(self, zone: str, action: str) -> None:
        return None


class _Controller:
    session = _Session()

    async def is_learning_active(self) -> bool:
        return False

    async def start_learning(self, zone: str, ac_power_before: float) -> None:
        return None


class _Executor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    async def call_entity_service(self, zone: str, turn_on: bool, *args: Any, **kwargs: Any) -> None:
        self.calls.append((zone, turn_on))


class _Services:
    def __init__(self, sink: list[tuple[str, str]]) -> None:
        self.sink = sink

    async def async_call(self, domain: str, service: str, *args: Any, **kwargs: Any) -> None:
        self.sink.append((domain, service))


class _Hass:
    def __init__(self, stopping: bool = False) -> None:
        self.loop = asyncio.get_event_loop()
        self.is_stopping = stopping
        self.service_calls: list[tuple[str, str]] = []
        self.services = _Services(self.service_calls)
        self.states = type("S", (), {"get": staticmethod(lambda eid: None)})()

    def async_create_task(self, coro: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)


def _coordinator(
    *,
    log_raises: BaseException | None = None,
    ramp: object = 0,
    stopping: bool = False,
) -> Any:
    c = object.__new__(SolarACCoordinator)
    c.config = {"zones": ["climate.a"]}
    c.hass = _Hass(stopping)  # type: ignore[assignment]
    c.controller = _Controller()  # type: ignore[assignment]
    c.action_executor = _Executor()  # type: ignore[assignment]
    c._state_lock = asyncio.Lock()
    c.action_delay_seconds = 0
    c.zone_last_changed = {}
    c.zone_last_changed_type = {}
    c.zone_last_context_id = {}
    c.active_zones = ["climate.a"]
    c.ema_30s = 0.0
    c.ema_5m = 0.0
    c.season_mode = "summer"
    c.compressor_ramp_seconds = ramp  # type: ignore[assignment]
    c.compressor_recover_until = 0
    c.last_action_start_ts = None
    c.last_action_duration = None

    async def _log(message: str, level: str = "info") -> None:
        if log_raises is not None:
            raise log_raises
        return None

    c._log = _log  # type: ignore[assignment, method-assign]
    c._record_zone_action = lambda *a, **k: None  # type: ignore[method-assign]
    return c


@pytest.mark.asyncio
async def test_recovery_window_set_even_when_log_fails() -> None:
    """The state write must survive a logging failure.

    Previously the write sat inside the logging guard, so a failing `_log`
    discarded it - and the recovery window is the short-cycle protection.
    """
    coord = _coordinator(log_raises=RuntimeError("log service down"), ramp=180)

    await ActionExecutor(coord).remove_zone("climate.a")

    assert coord.compressor_recover_until > 0, "the compressor recovery window was discarded because logging failed"


@pytest.mark.asyncio
async def test_recovery_window_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """A genuine failure to set the window must be reported, not swallowed."""
    coord = _coordinator(ramp=object())  # float() raises TypeError

    with caplog.at_level(logging.ERROR):
        await ActionExecutor(coord).remove_zone("climate.a")

    assert coord.compressor_recover_until == 0
    assert any("recovery window" in r.getMessage() for r in caplog.records), (
        "a failure to set the compressor recovery window was swallowed"
    )


@pytest.mark.asyncio
async def test_remove_zone_succeeds_when_log_fails() -> None:
    """A cosmetic log failure must not fail a completed removal.

    The zone is off and short-cycle tracking is updated before this point, so
    raising would report a successful operation as a failed cycle.
    """
    coord = _coordinator(log_raises=RuntimeError("log service down"))

    await ActionExecutor(coord).remove_zone("climate.a")

    assert coord.zone_last_changed_type["climate.a"] == "off"
    assert coord.hass.service_calls == [("climate", "turn_off")], "the zone was never switched off"


@pytest.mark.asyncio
async def test_add_zone_succeeds_when_log_fails() -> None:
    """Same contract on the add path."""
    coord = _coordinator(log_raises=RuntimeError("log service down"))

    await ActionExecutor(coord).add_zone("climate.a", 100.0)

    assert coord.zone_last_changed_type["climate.a"] == "on"


@pytest.mark.asyncio
async def test_add_zone_without_learning_succeeds_when_log_fails() -> None:
    """Same contract on the no-learning add path."""
    coord = _coordinator(log_raises=RuntimeError("log service down"))

    await ActionExecutor(coord).add_zone_without_learning("climate.a", 100.0)

    assert coord.zone_last_changed_type["climate.a"] == "on"


@pytest.mark.asyncio
async def test_cancellation_still_propagates_from_logging() -> None:
    """`CancelledError` is a BaseException and must not be absorbed."""
    coord = _coordinator(log_raises=asyncio.CancelledError(), ramp=180)

    with pytest.raises(asyncio.CancelledError):
        await ActionExecutor(coord).remove_zone("climate.a")


@pytest.mark.asyncio
async def test_invalid_zone_still_raises() -> None:
    """Validation must not be softened by the cosmetic logging change."""
    from homeassistant.exceptions import HomeAssistantError

    coord = _coordinator(log_raises=RuntimeError("log service down"))

    with pytest.raises(HomeAssistantError):
        await ActionExecutor(coord).remove_zone("climate.not_configured")


@pytest.mark.asyncio
async def test_success_path_is_quiet(caplog: pytest.LogCaptureFixture) -> None:
    """A healthy removal must not emit the failure message."""
    coord = _coordinator(ramp=180)

    with caplog.at_level(logging.ERROR):
        await ActionExecutor(coord).remove_zone("climate.a")

    assert not [r for r in caplog.records if "recovery window" in r.getMessage()]
