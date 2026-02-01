"""Binary sensor platform for BTicino Hometouch."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    """Set up BTicino Hometouch binary sensors."""
    coordinator: BticinoCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = [
        BticinoDoorbellSensor(coordinator, entry),
        BticinoConnectionSensor(coordinator, entry),
    ]

    async_add_entities(entities)


class BticinoDoorbellSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for doorbell ring detection."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(
        self,
        coordinator: BticinoCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_doorbell"
        self._attr_name = "Doorbell"
        self._attr_icon = "mdi:doorbell"
        # Set explicit entity_id for consistent naming
        self.entity_id = "binary_sensor.bticino_hometouch_doorbell"

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
    def is_on(self) -> bool:
        """Return True if someone is at the door (incoming call)."""
        return self.coordinator.has_active_call


class BticinoConnectionSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for SIP connection status."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: BticinoCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_connection"
        self._attr_name = "Connection"
        self._attr_icon = "mdi:lan-connect"
        # Set explicit entity_id for consistent naming
        self.entity_id = "binary_sensor.bticino_hometouch_connection"

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
    def is_on(self) -> bool:
        """Return True if connected and registered."""
        return self.coordinator.is_registered
