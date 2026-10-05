"""Suspend and Disable must actually stop the compressor relay.

Three defects are locked down here, all found against a live instance where
the master relay stayed energised through a 10-minute daytime suspension.

1. The freeze path turned off ZONES only. The relay was left to the ordinary
   solar-driven auto-control, which means a daytime freeze left the compressor
   energised - and a freeze taken while the relay was already off would
   actively switch it back ON, because solar was still above threshold_on.
   Reproduced against the unfixed code:

       disable at noon, relay ON  -> after disable(): master='on', no calls
       suspend at noon, relay OFF -> held-off cycle -> switch.turn_on

2. The relay is cut only once the compressor reads idle. Cutting mains while a
   zone is still running, or while the compressor is spinning down, is exactly
   what the safety gates exist to prevent. A user freeze must wait for them.

3. An unreadable AC power sensor means "not ready" for a USER freeze, not
   "unknown, proceed". The solar path may proceed because it has just decided
   solar is low; a user asking for the plant to stop may not. It logs and
   re-checks next cycle.

A fourth, the manual "on" lock, is covered too: it only releases when solar
climbs back above threshold_on, so at night it holds the relay on forever -
through a freeze included. A freeze outranks it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.solar_ac_controller.helpers import MasterSwitchController

ON_THRESHOLD = 1200.0
OFF_THRESHOLD = 500.0
IDLE = 15.8  # the learned idle baseline from the live instance
SPINDOWN_CUT = IDLE + 30.0  # 45.8 W


class _CM:
    """Config manager exposing only what handle_master_switch reads."""

    def __init__(self, zones: list[str] | None = None) -> None:
        self._zones = zones or []

    def get(self, key: str, default: Any = None) -> Any:
        if key == "ac_switch":
            return "switch.ac"
        return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        if key == "solar_threshold_on":
            return ON_THRESHOLD
        if key == "solar_threshold_off":
            return OFF_THRESHOLD
        return default

    def get_int(self, key: str, default: int = 0) -> int:
        return default

    def get_list(self, key: str, default: list[str] | None = None) -> list[str]:
        if key == "zones":
            return self._zones
        return default if default is not None else []


class _StateObj:
    def __init__(self, state: str) -> None:
        self.state = state


class _Hass:
    """Minimal hass: one switch entity plus a recording services.async_call."""

    def __init__(self, master: str, zone_states: dict[str, str] | None = None) -> None:
        self.master = master
        self.zone_states = zone_states or {}
        self.calls: list[str] = []
        outer = self

        class _States:
            def get(self, entity_id: str) -> _StateObj | None:
                if entity_id == "switch.ac":
                    return _StateObj(outer.master)
                if entity_id in outer.zone_states:
                    return _StateObj(outer.zone_states[entity_id])
                return None

        class _Services:
            async def async_call(self, domain: str, service: str, data: dict[str, Any], blocking: bool = False) -> None:
                outer.calls.append(service)
                outer.master = "off" if service == "turn_off" else "on"

        self.states = _States()
        self.services = _Services()


def _controller(
    *,
    master: str = "on",
    frozen: bool = True,
    zone_states: dict[str, str] | None = None,
    zones: list[str] | None = None,
    manual_lock: str | None = None,
) -> tuple[MasterSwitchController, _Hass, Any]:
    c = type("FakeCoordinator", (), {})()
    c.config_manager = _CM(zones=zones)
    c.hass = _Hass(master, zone_states)
    c._state_lock = asyncio.Lock()
    c.integration_enabled = not frozen
    c.master_last_state = None
    c.master_manual_lock_state = manual_lock
    c.master_commanded_state = None
    c.master_last_command_time = 0
    c.master_last_action_time = None
    c.master_off_since = None
    c.master_ema_reset_done = False
    c.learned_idle_power = IDLE
    c.idle_power_samples = 500
    c.last_action = None
    logs: list[str] = []
    c.logs = logs

    async def _log(message: str, level: str = "info") -> None:
        logs.append(message)

    c._log = _log
    return MasterSwitchController(c), c.hass, c


@pytest.mark.asyncio
async def test_daytime_freeze_cuts_the_relay_even_with_sun_above_threshold() -> None:
    """The whole defect: solar 3000 W must not keep the compressor alive."""
    mc, hass, _ = _controller(master="on", frozen=True)
    await mc.handle_master_switch(3000.0, 0.0, ac_power=SPINDOWN_CUT)
    assert hass.calls == ["turn_off"]
    assert hass.master == "off"


@pytest.mark.asyncio
async def test_freeze_never_switches_the_relay_on() -> None:
    """The dangerous direction: a freeze must never energise the relay."""
    mc, hass, _ = _controller(master="off", frozen=True)
    await mc.handle_master_switch(3000.0, 0.0, ac_power=SPINDOWN_CUT)
    assert "turn_on" not in hass.calls
    assert hass.master == "off"


@pytest.mark.asyncio
async def test_freeze_waits_for_compressor_to_reach_idle() -> None:
    """Above the idle band the compressor is still spinning down: do not cut."""
    mc, hass, _ = _controller(master="on", frozen=True)
    await mc.handle_master_switch(3000.0, 0.0, ac_power=SPINDOWN_CUT + 200.0)
    assert hass.calls == []
    assert hass.master == "on"


@pytest.mark.asyncio
async def test_freeze_cuts_once_power_settles_to_idle_on_a_later_cycle() -> None:
    """The deferral must converge, not stall forever."""
    mc, hass, _ = _controller(master="on", frozen=True)
    await mc.handle_master_switch(3000.0, 0.0, ac_power=SPINDOWN_CUT + 200.0)
    assert hass.calls == []
    await mc.handle_master_switch(3000.0, 0.0, ac_power=IDLE + 2.0)
    assert hass.calls == ["turn_off"]
    assert hass.master == "off"


@pytest.mark.asyncio
async def test_freeze_waits_while_a_zone_is_still_running() -> None:
    """Cutting mains under a running zone damages the compressor."""
    mc, hass, _ = _controller(
        master="on",
        frozen=True,
        zones=["climate.living"],
        zone_states={"climate.living": "heat"},
    )
    await mc.handle_master_switch(3000.0, 0.0, ac_power=IDLE)
    assert hass.calls == []
    assert hass.master == "on"


@pytest.mark.asyncio
async def test_unreadable_power_sensor_defers_rather_than_cutting_blind() -> None:
    """ac_power=None means 'unknown'. For a user freeze that is not ready."""
    mc, hass, _ = _controller(master="on", frozen=True)
    await mc.handle_master_switch(3000.0, 0.0, ac_power=None)
    assert hass.calls == []
    assert hass.master == "on"


@pytest.mark.asyncio
async def test_freeze_overrides_a_manual_on_lock() -> None:
    """An 'on' lock only releases at sunrise, so it would trap the relay all night."""
    mc, hass, coordinator = _controller(master="on", frozen=True, manual_lock="on")
    await mc.handle_master_switch(0.0, 0.0, ac_power=IDLE)
    assert coordinator.master_manual_lock_state is None
    assert hass.calls == ["turn_off"]
    assert hass.master == "off"


@pytest.mark.asyncio
async def test_freeze_does_not_disturb_an_already_off_relay() -> None:
    """Idempotent: nothing to do means no service call at all."""
    mc, hass, _ = _controller(master="off", frozen=True)
    await mc.handle_master_switch(3000.0, 0.0, ac_power=IDLE)
    assert hass.calls == []


@pytest.mark.asyncio
async def test_solar_path_is_unchanged_when_not_frozen() -> None:
    """Guard against the new branch swallowing ordinary auto-control."""
    mc, hass, _ = _controller(master="on", frozen=False)
    await mc.handle_master_switch(3000.0, 0.0, ac_power=IDLE)
    assert hass.calls == []

    mc, hass, _ = _controller(master="off", frozen=False)
    await mc.handle_master_switch(3000.0, 0.0, ac_power=IDLE)
    assert hass.calls == ["turn_on"]

    mc, hass, _ = _controller(master="on", frozen=False)
    await mc.handle_master_switch(0.0, 0.0, ac_power=IDLE)
    assert hass.calls == ["turn_off"]


@pytest.mark.asyncio
async def test_solar_path_still_cuts_with_an_unreadable_sensor() -> None:
    """The pre-existing lenient behaviour must be preserved for the solar path.

    The solar freeze has already concluded from solar alone, so an unreadable
    power sensor is not a new reason to hold the relay.
    """
    mc, hass, _ = _controller(master="on", frozen=False)
    await mc.handle_master_switch(0.0, 0.0, ac_power=None)
    assert hass.calls == ["turn_off"]
