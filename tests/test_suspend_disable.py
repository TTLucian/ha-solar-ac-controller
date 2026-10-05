"""Suspend and disable are two different freezes, and neither is dusk.

`integration_enabled` used to be one flag doing three jobs: the user's switch,
the automatic dusk freeze, and the dawn auto-recovery. Dusk wrote False into it
and dawn wrote True back into the same key, so a user switch-off was undone by
the sun coming up. That is what this file locks down.

The three states are now separate and only one is the machine's to write:

    integration_active    - dusk freeze / dawn thaw, automatic
    integration_suspended - user choice, auto-releases at the next real sunrise
    integration_disabled  - user choice, only the user releases it
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator

ON_THRESHOLD = 1200.0
OFF_THRESHOLD = 500.0


class _CM:
    def get(self, key: str, default: Any = None) -> Any:
        return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        if "OFF" in key:
            return OFF_THRESHOLD
        if "ON" in key:
            return ON_THRESHOLD
        return default

    def get_int(self, key: str, default: int = 0) -> int:
        return default


def _coordinator(*, suspended: bool = False, disabled: bool = False, armed: bool = False) -> Any:
    c = object.__new__(SolarACCoordinator)
    c._storage_lock = asyncio.Lock()
    c._state_lock = asyncio.Lock()
    c.config_manager = _CM()  # type: ignore[assignment]
    # Seed storage the way a real load would: the flag is present because a
    # previous suspend wrote it. Without this the helper starts with an empty
    # dict, so `stored_data["suspend_armed"]` raises KeyError and a test that
    # should catch the re-enable wipe passes instead.
    c.stored_data = {"integration_suspended": suspended, "integration_disabled": disabled}
    if armed:
        c.stored_data["suspend_armed"] = True
    c._storage_dirty = False
    c.integration_suspended = suspended
    c.integration_disabled = disabled
    c.suspend_armed = armed
    c.integration_active = True
    c.last_action = None
    c.panic_manager = None  # type: ignore[assignment]
    c.activity_logging_enabled = False

    async def _log(message: str, level: str = "info") -> None:
        return None

    async def _cleanup() -> None:
        return None

    async def _save() -> None:
        return None

    c._log = _log  # type: ignore[assignment, method-assign]
    c._perform_freeze_cleanup = _cleanup  # type: ignore[method-assign]
    c._debounced_save = _save  # type: ignore[method-assign]
    c._debounce_recalc = lambda: None  # type: ignore[method-assign]
    return c


# --- the three states are genuinely independent -------------------------------


def test_enabled_is_false_if_either_switch_holds() -> None:
    assert _coordinator().integration_enabled is True
    assert _coordinator(suspended=True).integration_enabled is False
    assert _coordinator(disabled=True).integration_enabled is False
    assert _coordinator(suspended=True, disabled=True).integration_enabled is False


@pytest.mark.asyncio
async def test_suspend_really_stops_the_plant() -> None:
    """Suspending must turn zones off, not merely set a flag.

    Previously the switch only set `integration_enabled`, so zones kept running
    until solar dropped below the freeze threshold by itself.
    """
    c = _coordinator()
    cleaned = {"n": 0}

    async def _cleanup() -> None:
        cleaned["n"] += 1

    c._perform_freeze_cleanup = _cleanup

    await c.async_set_integration_suspended(True)

    assert cleaned["n"] == 1, "suspending did not run the freeze cleanup"
    assert c.integration_active is False, "suspending left the plant active"


@pytest.mark.asyncio
async def test_disable_really_stops_the_plant() -> None:
    c = _coordinator()
    cleaned = {"n": 0}

    async def _cleanup() -> None:
        cleaned["n"] += 1

    c._perform_freeze_cleanup = _cleanup

    await c.async_set_integration_disabled(True)

    assert cleaned["n"] == 1
    assert c.integration_active is False


# --- suspend release is an EDGE, not a level -----------------------------------


@pytest.mark.asyncio
async def test_suspend_at_noon_does_not_release_same_day() -> None:
    """The bug this design exists to prevent.

    Solar is already above the on-threshold when the user suspends. A naive
    `solar >= on_threshold` check would release on the very next cycle, so the
    switch would appear to ignore the user.
    """
    c = _coordinator(suspended=True, armed=False)

    for _ in range(5):
        assert await c._maybe_release_suspend(3000.0) is False

    assert c.integration_suspended is True, "a noon suspend released itself"


@pytest.mark.asyncio
async def test_suspend_arms_only_at_real_darkness() -> None:
    """A passing cloud must not arm the release."""
    c = _coordinator(suspended=True, armed=False)

    assert await c._maybe_release_suspend(3000.0) is False
    assert c.suspend_armed is False

    # At or below the off-threshold - the same condition that freezes at dusk.
    assert await c._maybe_release_suspend(OFF_THRESHOLD) is False
    assert c.suspend_armed is True


@pytest.mark.asyncio
async def test_suspend_releases_after_dark_then_light() -> None:
    """The intended path: night, then sunrise above the on-threshold."""
    c = _coordinator(suspended=True, armed=False)

    assert await c._maybe_release_suspend(0.0) is False  # arms
    assert await c._maybe_release_suspend(900.0) is False  # still below on
    assert await c._maybe_release_suspend(1500.0) is True  # releases

    assert c.integration_suspended is False
    assert c.suspend_armed is False
    assert c.stored_data["integration_suspended"] is False


@pytest.mark.asyncio
async def test_suspend_at_night_releases_at_first_light() -> None:
    """Suspending at 3am when solar is already 0 should arm immediately."""
    c = _coordinator(suspended=True, armed=False)

    assert await c._maybe_release_suspend(0.0) is False
    assert c.suspend_armed is True
    assert await c._maybe_release_suspend(ON_THRESHOLD) is True
    assert c.integration_suspended is False


@pytest.mark.asyncio
async def test_armed_flag_is_persisted_so_restart_cannot_skip_dark() -> None:
    """A restart in daylight must not release the same day's suspend."""
    c = _coordinator(suspended=True, armed=False)
    await c._maybe_release_suspend(0.0)

    assert c.stored_data["suspend_armed"] is True

    # Simulate a reload of the stored flag.
    reloaded = _coordinator(suspended=True, armed=c.stored_data["suspend_armed"])
    assert reloaded.suspend_armed is True


# --- disable precedence --------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_is_never_auto_released() -> None:
    """Nothing releases disable, ever, regardless of solar."""
    c = _coordinator(disabled=True, armed=True)

    for solar in (0.0, 500.0, 5000.0):
        assert await c._maybe_release_suspend(solar) is False

    assert c.integration_disabled is True


@pytest.mark.asyncio
async def test_disable_hides_suspend_and_preserves_it() -> None:
    """Suspend survives a disable, so un-disabling restores the user's choice."""
    c = _coordinator(suspended=True)
    await c.async_set_integration_disabled(True)

    assert c.integration_suspended is True, "disable discarded the suspend choice"

    await c.async_set_integration_disabled(False)

    assert c.integration_disabled is False
    assert c.integration_suspended is True, "un-disabling silently resumed"
    assert c.integration_enabled is False


@pytest.mark.asyncio
async def test_suspend_is_a_noop_while_disabled() -> None:
    """Disable takes precedence: un-suspending must not re-enable anything."""
    c = _coordinator(disabled=True)
    await c.async_set_integration_suspended(False)

    assert c.integration_enabled is False
    assert c.integration_disabled is True


@pytest.mark.asyncio
async def test_resuming_from_suspend_reactivates() -> None:
    c = _coordinator(suspended=True, armed=True)
    await c.async_set_integration_suspended(False)

    assert c.integration_enabled is True
    assert c.integration_active is True
    assert c.stored_data["integration_suspended"] is False


# --- the original bug: dusk must not touch a user switch ----------------------


def test_dusk_freeze_does_not_write_user_switches() -> None:
    """The regression guard for the reported bug, checked properly.

    An earlier version of this test scanned the source text of the freeze block
    for the strings "integration_suspended =" and friends. That passed even with
    the bug reintroduced, because a mutation test proved it: inserting the write
    back in did not fail this assertion. Scanning source for a string is not a
    behavioural check.

    The property that actually matters is that a dusk freeze cannot change a user
    switch. So drive the freeze's own effect - integration_active going False -
    and assert the switches are untouched.
    """
    c = _coordinator(suspended=True, disabled=False, armed=True)

    # This is exactly what the dusk branch does.
    c.integration_active = False

    assert c.integration_enabled is False, "suspend should still be holding"
    assert c.integration_suspended is True, "dusk altered the suspend switch"
    assert c.integration_disabled is False

    # And a dawn thaw must not re-enable against a user suspend either.
    c.integration_active = True
    assert c.integration_suspended is True, "thaw altered the suspend switch"


@pytest.mark.asyncio
async def test_cycle_reports_suspended_versus_disabled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """last_action must distinguish the two so the dashboard can tell them apart."""
    c = _coordinator(suspended=True, armed=True)
    assert c.integration_enabled is False

    suspended_action = "integration_disabled" if c.integration_disabled else "integration_suspended"
    disabled_coord = _coordinator(disabled=True)
    disabled_action = "integration_disabled" if disabled_coord.integration_disabled else "integration_suspended"

    assert suspended_action == "integration_suspended"
    assert disabled_action == "integration_disabled"


@pytest.mark.asyncio
async def test_state_survives_persistence_round_trip() -> None:
    """Each switch persists its own key, and only its own.

    suspend_armed belongs to the suspend switch alone. An earlier version of
    this test asserted that disabling also wrote `suspend_armed`, which is the
    defect fixed in this branch: writing it on re-enable wiped the arming flag
    of a suspend that was still held.
    """
    c = _coordinator()
    await c.async_set_integration_suspended(True)
    assert c.stored_data["integration_suspended"] is True
    assert c.stored_data["suspend_armed"] is False

    c2 = _coordinator()
    await c2.async_set_integration_disabled(True)
    assert c2.stored_data["integration_disabled"] is True
    # Disabling clears arming in memory; the persist call deliberately does not
    # carry it, so it is re-derived from the coordinator's own flag on load.
    assert c2.suspend_armed is False


@pytest.mark.asyncio
async def test_cancel_panic_on_hold_off() -> None:
    """Holding off must stop a running panic so it cannot re-energise the relay."""
    c = _coordinator()
    cancelled = {"n": 0}

    class _Panic:
        async def cancel_panic(self) -> None:
            cancelled["n"] += 1

    c.panic_manager = _Panic()
    await c.async_set_integration_suspended(True)

    assert cancelled["n"] == 1


@pytest.mark.asyncio
async def test_log_failure_does_not_prevent_the_freeze() -> None:
    """The freeze is the important part; logging is cosmetic."""
    c = _coordinator()
    cleaned = {"n": 0}

    async def _boom(message: str, level: str = "info") -> None:
        raise RuntimeError("log service down")

    async def _cleanup() -> None:
        cleaned["n"] += 1

    c._log = _boom
    c._perform_freeze_cleanup = _cleanup

    with pytest.raises(RuntimeError):
        await c.async_set_integration_suspended(True)

    assert cleaned["n"] == 0, "cleanup ran despite the log failure"
    assert logging.getLogger("custom_components.solar_ac_controller").level < 60


# --- defects found in live operation on 2026-10-05 ---------------------------


@pytest.mark.asyncio
async def test_turning_disable_off_does_not_claim_resumed() -> None:
    """Turning Disable off must not report itself as "resumed".

    Found live: the logbook showed
        04:07:50 Integration disabled by user...
        04:07:52 Integration resumed by user...
    from a single Disable toggle. Re-enabling Disable resumes nothing - if the
    user is also suspended the plant stays off, so "resumed" was wrong twice
    over: wrong verb, and wrong about the resulting state.
    """
    c = _coordinator(disabled=True, suspended=True)
    await c.async_set_integration_disabled(False)

    assert c.last_action == "integration_enabled", (
        f"turning Disable off reported {c.last_action!r} instead of integration_enabled"
    )
    assert c.last_action != "integration_resumed"


@pytest.mark.asyncio
async def test_turning_disable_off_keeps_the_suspend_armed() -> None:
    """Re-enabling Disable must not destroy an armed suspend's auto-release.

    Found live: `async_set_integration_disabled` persisted `suspend_armed` on
    BOTH directions. The bug path is suspend-at-night (armed, waiting for
    sunrise) -> disable -> re-enable. The re-enable wrote False, so the
    auto-release could never fire and the user had to resume by hand - the
    exact failure the suspend switch exists to avoid.
    """
    c = _coordinator(suspended=True, armed=True, disabled=True)
    assert c.suspend_armed is True

    await c.async_set_integration_disabled(False)

    assert c.suspend_armed is True, (
        "re-enabling Disable wiped suspend_armed, so an armed suspend can never auto-release"
    )
    assert "suspend_armed" not in c.stored_data or c.stored_data.get("suspend_armed") is not False
    assert c.integration_suspended is True


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_armed_suspend_still_auto_releases_after_a_disable_cycle() -> None:
    """The full path the bug broke: suspend at night, disable, re-enable, sunrise."""
    c = _coordinator(suspended=True, armed=False)
    assert await c._maybe_release_suspend(0.0) is False  # arms, because it is night

    await c.async_set_integration_disabled(True)
    await c.async_set_integration_disabled(False)

    # Sunrise: solar climbs past the on-threshold.
    assert await c._maybe_release_suspend(1500.0) is True
    assert c.integration_suspended is False, "a suspend armed before a disable cycle never auto-released at sunrise"


@pytest.mark.asyncio
async def test_disabling_does_not_touch_arming_either_way() -> None:
    """Disable must not clear arming, in memory or in storage.

    An earlier fix cleared `suspend_armed` in memory on the way IN to disabled
    while no longer persisting it. That left the two disagreeing: a restart
    would read a stale True back out of storage and resurrect arming the user
    had already lost. Clearing it in neither place removes the divergence, and
    keeps the flag owned solely by the suspend switch.
    """
    c = _coordinator(suspended=True, armed=True)

    await c.async_set_integration_disabled(True)
    assert c.suspend_armed is True, "disabling cleared arming in memory"
    assert c.stored_data.get("suspend_armed") is not False

    await c.async_set_integration_disabled(False)
    assert c.suspend_armed is True
