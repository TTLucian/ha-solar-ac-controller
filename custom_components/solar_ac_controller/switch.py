"""
Switch entities for the Solar AC Controller integration.
"""

import logging
from functools import cached_property
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SolarACData

if TYPE_CHECKING:
    from .coordinator import SolarACCoordinator

_LOGGER = logging.getLogger(__name__)

INTEGRATION_SUSPEND_SWITCH = "integration_suspend"
INTEGRATION_DISABLE_SWITCH = "integration_disable"
# The pre-2.0 unique_id, kept only so the registry entry can be migrated in
# place. Without this, renaming the switch would orphan the old entity and any
# dashboard or automation pointing at it would silently stop working.
LEGACY_INTEGRATION_ENABLE_UNIQUE_ID = "integration_enable"
ACTIVITY_LOGGING_SWITCH = "activity_logging"


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Any) -> None:
    domain_data: SolarACData = hass.data[DOMAIN]
    coordinator = domain_data[entry.entry_id]["coordinator"]
    await _async_migrate_enable_switch(hass, entry)

    async_add_entities(
        [
            IntegrationSuspendSwitch(coordinator, entry),
            IntegrationDisableSwitch(coordinator, entry),
            ActivityLoggingSwitch(coordinator, entry),
        ]
    )


async def _async_migrate_enable_switch(hass: Any, entry: Any) -> None:
    """Move the old integration_enable entity onto integration_suspend.

    Home Assistant keys entities on unique_id, so simply renaming would create a
    brand new entity and leave the old one orphaned. Renaming in place keeps the
    entity_id and the history, so existing automations keep firing.
    """
    registry = er.async_get(hass)
    old_unique_id = f"{entry.entry_id}_{LEGACY_INTEGRATION_ENABLE_UNIQUE_ID}"
    new_unique_id = f"{entry.entry_id}_{INTEGRATION_SUSPEND_SWITCH}"
    existing = registry.async_get_entity_id("switch", DOMAIN, old_unique_id)
    if existing is None:
        return
    _LOGGER.debug("Migrating entity %s from unique_id %s to %s", existing, old_unique_id, new_unique_id)
    registry.async_update_entity(existing, new_unique_id=new_unique_id)


class IntegrationSuspendSwitch(  # pyright: ignore[reportIncompatibleVariableOverride]
    CoordinatorEntity, SwitchEntity
):
    """Freeze the plant until the next real sunrise, then resume automatically.

    Switch OFF means suspended. Suspension turns off the zones and drives the
    master relay off once the compressor reads idle; it auto-releases only on a
    genuine sunrise edge (see ``_maybe_release_suspend``), so suspending at noon
    does not undo itself on the next cycle.
    """

    coordinator: SolarACCoordinator

    @cached_property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name="Solar AC Controller",
        )

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Integration Suspend"
    _attr_icon = "mdi:pause"

    def __init__(self, coordinator: Any, entry: Any) -> None:
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{INTEGRATION_SUSPEND_SWITCH}"

    @property
    def is_on(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        return not getattr(self.coordinator, "integration_suspended", False)

    async def async_added_to_hass(self) -> None:
        """Hide this switch while the integration is disabled.

        Disable takes precedence, so there must never be two visible "off"
        controls to choose between. Hidden rather than merely unavailable: an
        unavailable switch still shows greyed in the UI, which reads as broken
        rather than as deliberately superseded.

        Hiding does mean an automation targeting this entity stops firing
        silently while disabled. That is the trade-off of hiding, and the reason
        it is stated here rather than left to be discovered.
        """
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self._sync_hidden))
        self._sync_hidden()

    def _sync_hidden(self) -> None:
        """Align the registry's hidden state with the disabled state.

        This HA version exposes the field as `hidden_by`, not `hidden`; the
        type checkers caught that, which is why it is not worth guessing at.
        `hidden` was a deprecated alias removed in recent releases.
        """
        should_hide = bool(getattr(self.coordinator, "integration_disabled", False))
        if not self.entity_id:
            return
        registry = er.async_get(self.hass)
        entry = registry.async_get(self.entity_id)
        if entry is None:
            return
        currently_hidden = entry.hidden_by is not None
        if currently_hidden != should_hide:
            registry.async_update_entity(
                self.entity_id,
                hidden_by=er.RegistryEntryHider.INTEGRATION if should_hide else None,
            )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_integration_suspended(False)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_integration_suspended(True)


class IntegrationDisableSwitch(  # pyright: ignore[reportIncompatibleVariableOverride]
    CoordinatorEntity, SwitchEntity
):
    """Freeze the plant indefinitely. Only the user releases it.

    Switch OFF means disabled. Like suspension it turns off the zones and drives
    the master relay off once the compressor reads idle, but nothing releases it -
    not the sun, and not turning it back off again. Turning it ON resumes; it does
    not "resume" anything if the suspend switch is still off, in which case the
    plant stays frozen. Disabling also hides the suspend switch.
    """

    coordinator: SolarACCoordinator

    @cached_property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name="Solar AC Controller",
        )

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Integration Disable"
    _attr_icon = "mdi:power-plug-off"

    def __init__(self, coordinator: Any, entry: Any) -> None:
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{INTEGRATION_DISABLE_SWITCH}"

    @property
    def is_on(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        return not getattr(self.coordinator, "integration_disabled", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_integration_disabled(False)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_integration_disabled(True)


class ActivityLoggingSwitch(  # pyright: ignore[reportIncompatibleVariableOverride]
    CoordinatorEntity, SwitchEntity
):
    coordinator: SolarACCoordinator
    _attr_entity_category = EntityCategory.CONFIG

    @cached_property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name="Solar AC Controller",
        )

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Activity Logging"
    _attr_icon = "mdi:text-box-outline"

    def __init__(self, coordinator: Any, entry: Any) -> None:
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_activity_logging"

    @property
    def is_on(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        return getattr(self.coordinator, "activity_logging_enabled", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_activity_logging_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_activity_logging_enabled(False)
