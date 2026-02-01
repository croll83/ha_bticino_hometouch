"""Data update coordinator for BTicino Hometouch."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any
from enum import Enum

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
    CONF_SIP_SERVER,
    CONF_SIP_PORT,
    CONF_SIP_USERNAME,
    CONF_SIP_PASSWORD,
    CONF_SIP_DOMAIN,
    CONF_GATEWAY_ADDRESS,
    CONF_CLIENT_CERT,
    CONF_CLIENT_KEY,
    CONF_CA_CERT,
    CONF_NUM_CAMERAS,
    CONF_NUM_LOCKS,
    CONF_LOCK_COMMANDS,
    CONF_APARTMENT_CODE,
    EVENT_INCOMING_CALL,
    EVENT_CALL_ENDED,
    DEFAULT_NUM_CAMERAS,
    DEFAULT_NUM_LOCKS,
    DEFAULT_APARTMENT_CODE,
)
from .sip_client import SIPClient, SIPConfig, SIPCall, CallState
from .media_proxy import MediaProxyManager

_LOGGER = logging.getLogger(__name__)


class IntercomState(Enum):
    """State of each intercom/outdoor station."""
    IDLE = "idle"
    RINGING = "ringing"
    CONNECTED = "connected"


class BticinoCoordinator(DataUpdateCoordinator):
    """Coordinator for BTicino Hometouch."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )
        self.entry = entry
        self._sip_client: SIPClient | None = None
        self._current_call: SIPCall | None = None
        self._incoming_call_event = asyncio.Event()
        self._media_proxy: MediaProxyManager | None = None

        # Parse configuration
        self._config = entry.data
        self._num_cameras = self._config.get(CONF_NUM_CAMERAS, DEFAULT_NUM_CAMERAS)
        self._num_locks = self._config.get(CONF_NUM_LOCKS, DEFAULT_NUM_LOCKS)
        self._lock_commands = self._config.get(CONF_LOCK_COMMANDS, ["A", "A", "A"])

        # State for each outdoor station/camera
        self._station_states: dict[int, IntercomState] = {
            i: IntercomState.IDLE for i in range(1, self._num_cameras + 1)
        }
        self._active_camera: int | None = None

        # Stream URLs (populated after media proxy starts)
        self._rtsp_urls: dict[int, str] = {}
        self._webrtc_urls: dict[int, str] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from SIP client."""
        call_state = None
        if self._current_call:
            call_state = self._current_call.state.value

        return {
            "registered": self._sip_client.is_registered if self._sip_client else False,
            "active_call": self._current_call is not None,
            "call_state": call_state,
            "active_camera": self._active_camera,
            "station_states": {k: v.value for k, v in self._station_states.items()},
            "rtsp_urls": self._rtsp_urls,
            "webrtc_urls": self._webrtc_urls,
        }

    async def async_start_sip(self) -> bool:
        """Start the SIP client and media proxy."""
        # Start media proxy first
        self._media_proxy = MediaProxyManager(
            config_dir=self.hass.config.path("bticino_hometouch"),
        )

        if not await self._media_proxy.start():
            _LOGGER.warning("Media proxy failed to start, video will not be available")
        else:
            # Pre-populate stream URLs
            for i in range(1, self._num_cameras + 1):
                self._rtsp_urls[i] = self._media_proxy.get_rtsp_url(i)
                self._webrtc_urls[i] = self._media_proxy.get_webrtc_url(i)

        # Start SIP client
        config = SIPConfig(
            server=self._config[CONF_SIP_SERVER],
            port=self._config[CONF_SIP_PORT],
            username=self._config[CONF_SIP_USERNAME],
            password=self._config[CONF_SIP_PASSWORD],
            domain=self._config[CONF_SIP_DOMAIN],
            gateway_address=self._config[CONF_GATEWAY_ADDRESS],
            client_cert=self._config[CONF_CLIENT_CERT],
            client_key=self._config[CONF_CLIENT_KEY],
            ca_cert=self._config[CONF_CA_CERT],
            apartment_code=self._config.get(CONF_APARTMENT_CODE, DEFAULT_APARTMENT_CODE),
        )

        self._sip_client = SIPClient(
            config,
            on_incoming_call=self._on_incoming_call,
            on_call_state_changed=self._on_call_state_changed,
            on_video_frame=self._on_video_frame,
        )

        if await self._sip_client.connect():
            if await self._sip_client.register():
                _LOGGER.info("BTicino SIP client started and registered")
                return True

        _LOGGER.error("Failed to start BTicino SIP client")
        return False

    async def async_stop_sip(self):
        """Stop the SIP client and media proxy."""
        if self._sip_client:
            await self._sip_client.disconnect()
            self._sip_client = None

        if self._media_proxy:
            await self._media_proxy.stop()
            self._media_proxy = None

    @callback
    def _on_incoming_call(self, call: SIPCall):
        """Handle incoming call."""
        self._current_call = call
        self._incoming_call_event.set()

        # Determine which station is calling (from caller URI or DEVADDR)
        # Default to station 1 if we can't determine
        station_id = self._parse_station_id(call.remote_uri) or 1
        self._active_camera = station_id

        # Update station state
        for sid in self._station_states:
            if sid == station_id:
                self._station_states[sid] = IntercomState.RINGING
            else:
                self._station_states[sid] = IntercomState.IDLE

        # Fire event for automations
        self.hass.bus.async_fire(
            EVENT_INCOMING_CALL,
            {
                "call_id": call.call_id,
                "caller": call.remote_uri,
                "station_id": station_id,
            },
        )

        # Send push notification via HA companion app
        self.hass.async_create_task(self._send_notification(call, station_id))

        # Trigger coordinator update
        self.async_set_updated_data(self.data)

        _LOGGER.info("Incoming call from station %d (%s)", station_id, call.remote_uri)

    def _parse_station_id(self, remote_uri: str) -> int | None:
        """Parse station ID from caller URI."""
        # The URI might contain station info like "sip:station1@gateway"
        # This depends on the specific BTicino installation
        try:
            if "station" in remote_uri.lower():
                import re
                match = re.search(r'station(\d+)', remote_uri.lower())
                if match:
                    return int(match.group(1))
        except Exception:
            pass
        return None

    @callback
    def _on_call_state_changed(self, call: SIPCall, state: CallState):
        """Handle call state change."""
        _LOGGER.info("Call state changed to %s", state)

        if state == CallState.CONNECTED:
            # Update station state to connected
            if self._active_camera:
                self._station_states[self._active_camera] = IntercomState.CONNECTED

            # Setup video stream
            self.hass.async_create_task(self._setup_video_stream(call))

        elif state == CallState.DISCONNECTED:
            # Reset all states
            for sid in self._station_states:
                self._station_states[sid] = IntercomState.IDLE
            self._active_camera = None
            self._current_call = None
            self._incoming_call_event.clear()

            # Teardown video stream
            self.hass.async_create_task(self._teardown_video_stream())

            # Fire event
            self.hass.bus.async_fire(
                EVENT_CALL_ENDED,
                {
                    "call_id": call.call_id,
                },
            )

        # Trigger update
        self.async_set_updated_data(self.data)

    @callback
    def _on_video_frame(self, frame_data: bytes):
        """Handle incoming video frame (for media proxy)."""
        # This is called when raw video data is received
        # The media proxy will handle it
        pass

    async def _setup_video_stream(self, call: SIPCall):
        """Setup video stream through media proxy."""
        if not self._media_proxy or not self._active_camera:
            return

        try:
            # Extract SRTP parameters from SDP in the call
            srtp_port = call.video_rtp_port
            srtp_key = call.video_srtp_key

            if srtp_port and srtp_key:
                rtsp_url = await self._media_proxy.setup_camera_stream(
                    camera_id=self._active_camera,
                    srtp_port=srtp_port,
                    srtp_key=srtp_key,
                )
                self._rtsp_urls[self._active_camera] = rtsp_url
                _LOGGER.info("Video stream ready at %s", rtsp_url)
        except Exception as e:
            _LOGGER.error("Failed to setup video stream: %s", e)

    async def _teardown_video_stream(self):
        """Teardown video stream."""
        if self._media_proxy and self._active_camera:
            await self._media_proxy.teardown_stream(f"bticino_camera_{self._active_camera}")

    async def _send_notification(self, call: SIPCall, station_id: int):
        """Send push notification via Home Assistant companion app."""
        # Get camera image URL
        camera_entity = f"camera.bticino_hometouch_outdoor_station_{station_id}"

        await self.hass.services.async_call(
            "notify",
            "mobile_app",
            {
                "title": "🔔 Videocitofono",
                "message": f"Chiamata dal posto esterno {station_id}",
                "data": {
                    "tag": f"intercom_{call.call_id}",
                    "group": "bticino_hometouch",
                    "channel": "doorbell",
                    "importance": "high",
                    "priority": "high",
                    "ttl": 0,
                    "actions": [
                        {
                            "action": "ANSWER_CALL",
                            "title": "📞 Rispondi",
                        },
                        {
                            "action": f"UNLOCK_DOOR_{station_id}",
                            "title": "🔓 Apri",
                        },
                        {
                            "action": "REJECT_CALL",
                            "title": "❌ Rifiuta",
                        },
                    ],
                    "image": f"/api/camera_proxy/{camera_entity}",
                    "entity_id": camera_entity,
                    "push": {
                        "sound": {
                            "name": "default",
                            "critical": 1,
                            "volume": 1.0,
                        },
                        "interruption-level": "critical",
                    },
                    # For Android
                    "notification_icon": "mdi:doorbell-video",
                    "color": "#FF5722",
                    "vibrationPattern": "100, 200, 100, 200, 100",
                },
            },
        )

    async def async_unlock_door(self, lock_id: int) -> bool:
        """Unlock a door."""
        if not self._sip_client or not self._sip_client.is_registered:
            _LOGGER.error("SIP client not registered")
            return False

        # Get command type for this lock
        if lock_id < 1 or lock_id > len(self._lock_commands):
            command_type = "A"
        else:
            command_type = self._lock_commands[lock_id - 1]

        return await self._sip_client.send_door_unlock(lock_id, command_type)

    async def async_answer_call(self) -> bool:
        """Answer the current incoming call."""
        if not self._sip_client or not self._current_call:
            return False

        success = await self._sip_client.answer_call(self._current_call.call_id)
        if success and self._active_camera:
            self._station_states[self._active_camera] = IntercomState.CONNECTED
            self.async_set_updated_data(self.data)
        return success

    async def async_hangup_call(self) -> bool:
        """Hangup the current call."""
        if not self._sip_client or not self._current_call:
            return False

        return await self._sip_client.hangup_call(self._current_call.call_id)

    async def async_switch_camera(self, camera_id: int) -> bool:
        """Switch to a different camera."""
        if not self._sip_client or not self._current_call:
            return False

        success = await self._sip_client.switch_camera(
            self._current_call.call_id,
            str(camera_id),
        )

        if success:
            self._active_camera = camera_id
            self.async_set_updated_data(self.data)

        return success

    async def async_initiate_call(self, station_id: int) -> bool:
        """Initiate a call to an outdoor station (view camera on demand)."""
        if not self._sip_client or not self._sip_client.is_registered:
            _LOGGER.error("SIP client not registered")
            return False

        if self._current_call:
            _LOGGER.warning("Call already in progress")
            return False

        # TODO: Implement outgoing call to view camera
        # This requires understanding the SIP address format for each station
        # For now, return False as this feature needs more reverse engineering
        _LOGGER.info("Initiating call to station %d", station_id)
        return False

    @property
    def is_registered(self) -> bool:
        """Return True if SIP client is registered."""
        return self._sip_client is not None and self._sip_client.is_registered

    @property
    def has_active_call(self) -> bool:
        """Return True if there's an active call."""
        return self._current_call is not None

    @property
    def call_state(self) -> CallState | None:
        """Return current call state."""
        return self._current_call.state if self._current_call else None

    @property
    def current_call(self) -> SIPCall | None:
        """Return the current call."""
        return self._current_call

    @property
    def num_cameras(self) -> int:
        """Return number of cameras."""
        return self._num_cameras

    @property
    def num_locks(self) -> int:
        """Return number of locks."""
        return self._num_locks

    @property
    def active_camera(self) -> int | None:
        """Return the currently active camera."""
        return self._active_camera

    def get_station_state(self, station_id: int) -> IntercomState:
        """Get the state of a specific station."""
        return self._station_states.get(station_id, IntercomState.IDLE)

    def get_rtsp_url(self, camera_id: int) -> str | None:
        """Get RTSP URL for a camera."""
        return self._rtsp_urls.get(camera_id)

    def get_webrtc_url(self, camera_id: int) -> str | None:
        """Get WebRTC URL for a camera."""
        return self._webrtc_urls.get(camera_id)
