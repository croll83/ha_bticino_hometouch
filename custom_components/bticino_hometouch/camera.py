"""Camera platform for BTicino Hometouch."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BticinoCoordinator, IntercomState

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BTicino Hometouch cameras."""
    coordinator: BticinoCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[Camera] = []

    # Add camera entities for each camera
    for camera_id in range(1, coordinator.num_cameras + 1):
        entities.append(BticinoIntercomCamera(coordinator, entry, camera_id))

    async_add_entities(entities)


class BticinoIntercomCamera(CoordinatorEntity, Camera):
    """Camera entity for BTicino Hometouch.

    This camera supports WebRTC for low-latency video streaming with
    bidirectional audio when there's an active call.
    """

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature.STREAM | CameraEntityFeature.ON_OFF

    def __init__(
        self,
        coordinator: BticinoCoordinator,
        entry: ConfigEntry,
        camera_id: int,
    ) -> None:
        """Initialize the camera."""
        super().__init__(coordinator)
        Camera.__init__(self)
        self._camera_id = camera_id
        self._attr_unique_id = f"{entry.entry_id}_camera_{camera_id}"
        self._attr_name = f"Outdoor Station {camera_id}"
        self._attr_is_streaming = False
        self._last_image: bytes | None = None
        # Set explicit entity_id for consistent naming
        self.entity_id = f"camera.bticino_hometouch_outdoor_station_{camera_id}"

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
        """Return True if this camera is active (during call from this station)."""
        return self.coordinator.active_camera == self._camera_id

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.is_registered

    @property
    def is_recording(self) -> bool:
        """Return True if there's an active call."""
        return self.coordinator.has_active_call and self.coordinator.active_camera == self._camera_id

    @property
    def motion_detection_enabled(self) -> bool:
        """Return True if motion detection is enabled (incoming call = motion)."""
        state = self.coordinator.get_station_state(self._camera_id)
        return state == IntercomState.RINGING

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        state = self.coordinator.get_station_state(self._camera_id)
        return {
            "station_state": state.value,
            "is_ringing": state == IntercomState.RINGING,
            "is_connected": state == IntercomState.CONNECTED,
            "rtsp_url": self.coordinator.get_rtsp_url(self._camera_id),
            "webrtc_url": self.coordinator.get_webrtc_url(self._camera_id),
        }

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the camera.

        Note: Images are only available during active calls since video
        comes via SRTP which requires SIP call establishment.
        """
        if not self.coordinator.has_active_call:
            return self._last_image

        # TODO: Capture a frame from the stream
        return self._last_image

    async def stream_source(self) -> str | None:
        """Return the RTSP stream source URL.

        The stream is provided by go2rtc which handles SRTP decryption.
        """
        if not self.coordinator.has_active_call:
            return None

        if self.coordinator.active_camera != self._camera_id:
            return None

        return self.coordinator.get_rtsp_url(self._camera_id)

    @property
    def frontend_stream_type(self) -> str | None:
        """Return the type of stream for the frontend.

        Returns 'webrtc' for low-latency streaming with bidirectional audio.
        """
        if self.coordinator.has_active_call and self.coordinator.active_camera == self._camera_id:
            return "webrtc"
        return None

    async def async_handle_web_rtc_offer(self, offer_sdp: str) -> str | None:
        """Handle WebRTC offer from frontend.

        This enables bidirectional audio through WebRTC.
        """
        # The WebRTC negotiation is handled by go2rtc
        # We need to forward the offer to go2rtc and return the answer
        webrtc_url = self.coordinator.get_webrtc_url(self._camera_id)
        if not webrtc_url:
            return None

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webrtc_url,
                    json={"type": "offer", "sdp": offer_sdp},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("sdp")
        except Exception as e:
            _LOGGER.error("WebRTC negotiation failed: %s", e)

        return None

    async def async_turn_on(self) -> None:
        """Turn on the camera (switch to this camera during active call)."""
        if self.coordinator.has_active_call:
            await self.coordinator.async_switch_camera(self._camera_id)
            _LOGGER.info("Switched to camera %d", self._camera_id)
        else:
            # Initiate call to this station to view the camera
            await self.coordinator.async_initiate_call(self._camera_id)
            _LOGGER.info("Initiated call to station %d", self._camera_id)

    async def async_turn_off(self) -> None:
        """Turn off the camera (hangup call)."""
        if self.coordinator.has_active_call:
            await self.coordinator.async_hangup_call()
