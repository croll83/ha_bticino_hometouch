"""Button platform for BTicino Hometouch."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BticinoCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BTicino Hometouch buttons."""
    coordinator: BticinoCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[ButtonEntity] = []

    # Add door lock buttons
    for lock_id in range(1, coordinator.num_locks + 1):
        entities.append(BticinoDoorLockButton(coordinator, entry, lock_id))

    # Add call control buttons
    entities.append(BticinoAnswerCallButton(coordinator, entry))
    entities.append(BticinoHangupCallButton(coordinator, entry))

    async_add_entities(entities)


class BticinoDoorLockButton(CoordinatorEntity, ButtonEntity):
    """Button to unlock a door."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BticinoCoordinator,
        entry: ConfigEntry,
        lock_id: int,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._lock_id = lock_id
        self._attr_unique_id = f"{entry.entry_id}_lock_{lock_id}"
        self._attr_name = f"Unlock Door {lock_id}"
        self._attr_icon = "mdi:door-open"
        # Set explicit entity_id for consistent naming
        self.entity_id = f"button.bticino_hometouch_unlock_door_{lock_id}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.entry.entry_id)},
            name="BTicino Hometouch",
            manufacturer="BTicino",
            model="Door Entry Touch",
        )

    async def async_press(self) -> None:
        """Handle button press."""
        _LOGGER.info("Unlocking door %d", self._lock_id)
        success = await self.coordinator.async_unlock_door(self._lock_id)
        if not success:
            _LOGGER.error("Failed to unlock door %d", self._lock_id)


class BticinoAnswerCallButton(CoordinatorEntity, ButtonEntity):
    """Button to answer an incoming call."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BticinoCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_answer_call"
        self._attr_name = "Answer Call"
        self._attr_icon = "mdi:phone"
        # Set explicit entity_id for consistent naming
        self.entity_id = "button.bticino_hometouch_answer_call"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.entry.entry_id)},
            name="BTicino Hometouch",
            manufacturer="BTicino",
            model="Door Entry Touch",
        )

    @property
    def available(self) -> bool:
        """Return True if there's an incoming call."""
        return self.coordinator.has_active_call

    async def async_press(self) -> None:
        """Handle button press."""
        _LOGGER.info("Answering call")
        success = await self.coordinator.async_answer_call()
        if not success:
            _LOGGER.error("Failed to answer call")


class BticinoHangupCallButton(CoordinatorEntity, ButtonEntity):
    """Button to hangup a call."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BticinoCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_hangup_call"
        self._attr_name = "Hangup Call"
        self._attr_icon = "mdi:phone-hangup"
        # Set explicit entity_id for consistent naming
        self.entity_id = "button.bticino_hometouch_hangup_call"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.entry.entry_id)},
            name="BTicino Hometouch",
            manufacturer="BTicino",
            model="Door Entry Touch",
        )

    @property
    def available(self) -> bool:
        """Return True if there's an active call."""
        return self.coordinator.has_active_call

    async def async_press(self) -> None:
        """Handle button press."""
        _LOGGER.info("Hanging up call")
        success = await self.coordinator.async_hangup_call()
        if not success:
            _LOGGER.error("Failed to hangup call")
