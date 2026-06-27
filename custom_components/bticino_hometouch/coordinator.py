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
    CONF_LOCK_ADDRESSES,
    CONF_APARTMENT_CODE,
    CONF_PUBLIC_IP,
    EVENT_INCOMING_CALL,
    EVENT_CALL_ENDED,
    EVENT_DOOR_UNLOCKED,
    DEFAULT_NUM_CAMERAS,
    DEFAULT_NUM_LOCKS,
    DEFAULT_APARTMENT_CODE,
    DEFAULT_LOCK_ADDRESSES,
)
from .sip_client import SIPClient, SIPConfig, SIPCall, CallState, MediaMode
from .const import CONF_DEVICE_TYPE, DEFAULT_DEVICE_TYPE, SIP_RECIPIENT_BY_TYPE
from .media_proxy import MediaProxyManager, BidirectionalAudio

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
        self._call_lock = asyncio.Lock()  # Prevent concurrent call attempts

        # Parse configuration
        self._config = entry.data
        self._num_cameras = self._config.get(CONF_NUM_CAMERAS, DEFAULT_NUM_CAMERAS)
        self._num_locks = self._config.get(CONF_NUM_LOCKS, DEFAULT_NUM_LOCKS)
        self._lock_commands = self._config.get(CONF_LOCK_COMMANDS, ["A", "A", "A"])
        self._lock_addresses = self._config.get(CONF_LOCK_ADDRESSES, DEFAULT_LOCK_ADDRESSES)

        # State for each outdoor station/camera
        self._station_states: dict[int, IntercomState] = {
            i: IntercomState.IDLE for i in range(1, self._num_cameras + 1)
        }
        self._active_camera: int | None = None

        # Stream URLs (populated after media proxy starts)
        self._rtsp_urls: dict[int, str] = {}
        self._webrtc_urls: dict[int, str] = {}

        # Last error for each station (to communicate to frontend)
        self._last_errors: dict[int, str | None] = {
            i: None for i in range(1, self._num_cameras + 1)
        }

        # Stream availability for each station (HLS or WebRTC, updated asynchronously)
        self._stream_available: dict[int, bool] = {
            i: False for i in range(1, self._num_cameras + 1)
        }

        # Legacy alias for backwards compatibility
        self._hls_available = self._stream_available

        # Audio handler for bidirectional audio
        self._audio_handler = None
        self._audio_enabled = False

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from SIP client."""
        call_state = None
        if self._current_call:
            call_state = self._current_call.state.value

        # Check connection health
        sip_healthy = False
        sip_registered = False
        connection_age = 0.0

        if self._sip_client:
            sip_registered = self._sip_client.is_registered
            sip_healthy = self._sip_client.is_healthy
            connection_age = self._sip_client.connection_age

            # Log warning if registered but not healthy (connection may be stale)
            if sip_registered and not sip_healthy:
                _LOGGER.warning(
                    "SIP registered but connection unhealthy (no data for %.0f seconds)",
                    connection_age
                )

        return {
            "registered": sip_registered,
            "healthy": sip_healthy,
            "connection_age": connection_age,
            "active_call": self._current_call is not None,
            "call_state": call_state,
            "active_camera": self._active_camera,
            "station_states": {k: v.value for k, v in self._station_states.items()},
            "rtsp_urls": self._rtsp_urls,
            "webrtc_urls": self._webrtc_urls,
            "last_errors": self._last_errors,
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
        # Get public IP - if not configured, try to auto-detect
        public_ip = self._config.get(CONF_PUBLIC_IP, "")
        if not public_ip:
            # Try to auto-detect public IP
            public_ip = await self._detect_public_ip()

        _LOGGER.info("Using public IP for NAT traversal: %s", public_ip or "(none - using local)")

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
            public_ip=public_ip,
            recipient_user=SIP_RECIPIENT_BY_TYPE.get(self._config.get(CONF_DEVICE_TYPE, DEFAULT_DEVICE_TYPE), "MHT"),
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

    async def _detect_public_ip(self) -> str:
        """Try to detect public IP address using external services."""
        import aiohttp

        services = [
            "https://api.ipify.org",
            "https://ifconfig.me/ip",
            "https://icanhazip.com",
            "https://checkip.amazonaws.com",
        ]

        async with aiohttp.ClientSession() as session:
            for service in services:
                try:
                    async with session.get(service, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            ip = (await resp.text()).strip()
                            # Validate it looks like an IP
                            if ip and "." in ip and len(ip) <= 15:
                                _LOGGER.info("Auto-detected public IP: %s (from %s)", ip, service)
                                return ip
                except Exception as e:
                    _LOGGER.debug("Failed to get public IP from %s: %s", service, e)
                    continue

        _LOGGER.warning("Could not auto-detect public IP. Configure it in integration options for video streaming.")
        return ""

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

            # Note: Video stream setup is handled by async_initiate_call()
            # Don't call _setup_video_stream here to avoid duplicate setup

        elif state == CallState.DISCONNECTED:
            # Save active camera before resetting for teardown
            camera_to_teardown = self._active_camera

            # Check if call was ever connected (had video stream)
            # If not connected, this is a failed call attempt (e.g., 486 Busy)
            # and we shouldn't fire call_ended event
            was_connected = camera_to_teardown and camera_to_teardown in [
                s for s, state in self._station_states.items()
                if state == IntercomState.CONNECTED
            ]

            # Reset all states
            for sid in self._station_states:
                self._station_states[sid] = IntercomState.IDLE
            self._active_camera = None
            self._current_call = None
            self._incoming_call_event.clear()

            # Teardown video stream (using saved camera ID)
            if camera_to_teardown:
                self.hass.async_create_task(self._teardown_video_stream_for_camera(camera_to_teardown))

            # Fire event with station_id for frontend ONLY if call was connected
            # Don't fire for failed call attempts (486, timeout, etc.)
            if was_connected:
                self.hass.bus.async_fire(
                    EVENT_CALL_ENDED,
                    {
                        "call_id": call.call_id,
                        "station_id": camera_to_teardown,
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
            _LOGGER.warning("Cannot setup video stream: media_proxy=%s, active_camera=%s",
                          self._media_proxy is not None, self._active_camera)
            return

        try:
            # Extract SRTP parameters from SDP in the call
            # video_srtp_key should be the SERVER's key (for decrypting incoming video)
            srtp_port = call.video_rtp_port
            srtp_key = call.video_srtp_key

            import base64
            key_b64 = base64.b64encode(srtp_key).decode()[:20] if srtp_key else "none"
            _LOGGER.info("Setting up video stream: port=%s, key=%s bytes (b64=%s...)",
                        srtp_port, len(srtp_key) if srtp_key else 0, key_b64)

            if srtp_port and srtp_key:
                rtsp_url = await self._media_proxy.setup_camera_stream(
                    camera_id=self._active_camera,
                    srtp_port=srtp_port,
                    srtp_key=srtp_key,
                )
                self._rtsp_urls[self._active_camera] = rtsp_url
                _LOGGER.info("Video stream setup initiated: %s", rtsp_url)

                # Wait for HLS file to be created and then notify entities
                # This runs in background to avoid blocking the call setup
                self.hass.async_create_task(
                    self._wait_for_hls_and_notify(self._active_camera)
                )
            else:
                _LOGGER.warning("Missing SRTP parameters: port=%s, key_len=%s",
                              srtp_port, len(srtp_key) if srtp_key else 0)
        except Exception as e:
            _LOGGER.error("Failed to setup video stream: %s", e)
            import traceback
            _LOGGER.debug(traceback.format_exc())

    async def _wait_for_stream_and_notify(self, camera_id: int, timeout: float = 15.0):
        """Wait for stream to be ready and then force state update.

        In WebRTC mode: Checks go2rtc for active producers.
        In HLS mode: Checks for HLS file creation.

        This ensures the camera entity's extra_state_attributes reflect
        stream availability after the stream is ready.
        """
        webrtc_mode = self.is_webrtc_mode()
        _LOGGER.info("Waiting for stream (webrtc_mode=%s, camera=%d, timeout=%ds)",
                    webrtc_mode, camera_id, timeout)

        start_time = asyncio.get_event_loop().time()
        check_interval = 0.5  # Check every 500ms

        while (asyncio.get_event_loop().time() - start_time) < timeout:
            try:
                if webrtc_mode:
                    # WebRTC mode: check go2rtc for active stream
                    is_ready = await self._media_proxy.is_stream_active(camera_id)
                else:
                    # HLS mode: check filesystem
                    is_ready = await asyncio.to_thread(self._check_hls_ready, camera_id)

                if is_ready:
                    mode_str = "WebRTC" if webrtc_mode else "HLS"
                    _LOGGER.info("%s stream ready for camera %d", mode_str, camera_id)
                    # Update stream availability state
                    self._stream_available[camera_id] = True
                    # Force entity state update
                    self.async_set_updated_data(self.data)
                    return
            except Exception as e:
                _LOGGER.debug("Error checking stream: %s", e)

            await asyncio.sleep(check_interval)

        _LOGGER.warning("Timeout waiting for stream for camera %d", camera_id)

    def _check_hls_ready(self, camera_id: int) -> bool:
        """Check if HLS is ready (m3u8 exists and has segments)."""
        import os
        hls_dir = "/config/www/bticino"
        m3u8_file = f"bticino_camera_{camera_id}.m3u8"
        segment_prefix = f"bticino_camera_{camera_id}_"

        m3u8_path = os.path.join(hls_dir, m3u8_file)
        if not os.path.exists(m3u8_path):
            return False
        # Check if any .ts segment files exist
        try:
            for entry in os.scandir(hls_dir):
                if entry.name.startswith(segment_prefix) and entry.name.endswith('.ts'):
                    return True
        except Exception:
            pass
        return False

    # Backwards compatibility alias
    async def _wait_for_hls_and_notify(self, camera_id: int, timeout: float = 15.0):
        """Alias for _wait_for_stream_and_notify (backwards compatibility)."""
        await self._wait_for_stream_and_notify(camera_id, timeout)

    async def _teardown_video_stream(self):
        """Teardown video stream for the currently active camera."""
        if self._media_proxy and self._active_camera:
            await self._media_proxy.teardown_stream(f"bticino_live_{self._active_camera}")
            # Mark stream as unavailable
            self._stream_available[self._active_camera] = False

    async def _teardown_video_stream_for_camera(self, camera_id: int):
        """Teardown video stream for a specific camera."""
        if self._media_proxy:
            await self._media_proxy.teardown_stream(f"bticino_live_{camera_id}")
            # Mark stream as unavailable
            self._stream_available[camera_id] = False

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

    async def _ensure_sip_ready(self, force: bool = False) -> bool:
        """Make sure the SIP client is connected & registered before a command.

        If the connection is stale (or force=True) tear it down and reconnect
        synchronously, so commands aren't sent into a dead TLS socket — this is
        what makes the door open reliably instead of ~1 in 50 times.
        """
        if not self._sip_client:
            return False
        if not force and self._sip_client.is_healthy:
            return True
        _LOGGER.info(
            "Reconnecting SIP before command (force=%s, registered=%s, connected=%s, age=%.0fs)",
            force, self._sip_client.is_registered,
            self._sip_client.is_connected, self._sip_client.connection_age,
        )
        try:
            await self._sip_client.disconnect()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("disconnect during ensure_sip_ready raised: %s", err)
        try:
            return bool(await self._sip_client.connect() and await self._sip_client.register())
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("SIP reconnect failed: %s", err)
            return False

    async def async_unlock_door(self, lock_id: int) -> bool:
        """Unlock a door."""
        if not self._sip_client:
            _LOGGER.error("SIP client not initialized")
            return False

        # Ensure a live, registered SIP connection before sending — a stale TLS
        # socket (registered=True but dead) would otherwise swallow the command.
        if not await self._ensure_sip_ready():
            _LOGGER.error("Cannot unlock door %d: SIP reconnect failed", lock_id)
            return False

        # Get command type for this lock
        if lock_id < 1 or lock_id > len(self._lock_commands):
            command_type = "A"
        else:
            command_type = self._lock_commands[lock_id - 1]

        # Get the actual lock address (OpenWebNet WHERE)
        # If lock_addresses is configured, use it; otherwise use lock_id
        if self._lock_addresses and lock_id <= len(self._lock_addresses):
            lock_address = self._lock_addresses[lock_id - 1]
            _LOGGER.info("Lock %d -> address %d (from config: %s)", lock_id, lock_address, self._lock_addresses)
        else:
            lock_address = lock_id
            _LOGGER.info("Lock %d -> address %d (using lock_id as address, no config)", lock_id, lock_address)

        success = await self._sip_client.send_door_unlock(lock_address, command_type)

        if not success:
            # Stale-but-"healthy" case: force a fresh reconnect and retry once,
            # mirroring how the BTicino app re-registers on every open.
            _LOGGER.warning("Door %d unlock failed, forcing reconnect and retrying once", lock_id)
            if await self._ensure_sip_ready(force=True):
                success = await self._sip_client.send_door_unlock(lock_address, command_type)

        if success:
            # Fire event for UI feedback (200 OK received from door station)
            self.hass.bus.async_fire(
                EVENT_DOOR_UNLOCKED,
                {
                    "lock_id": lock_id,
                },
            )
            _LOGGER.info("Door %d unlocked successfully (200 OK received)", lock_id)

        return success

    async def async_answer_call(self) -> bool:
        """Answer the current incoming call.

        This also sets up the video stream for incoming calls.
        """
        if not self._sip_client or not self._current_call:
            return False

        success = await self._sip_client.answer_call(self._current_call.call_id)
        if success and self._active_camera:
            self._station_states[self._active_camera] = IntercomState.CONNECTED

            # Setup video stream for incoming call
            # The SIP answer should have negotiated media, so we can now setup the stream
            await self._setup_video_stream(self._current_call)

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

    async def async_initiate_call(
        self,
        station_id: int,
        media_mode: MediaMode = MediaMode.VIDEO_ONLY
    ) -> bool:
        """Initiate a call to an outdoor station.

        Args:
            station_id: The station number (1, 2, 3...)
            media_mode: VIDEO_ONLY for camera, AUDIO_ONLY for intercom, AUDIO_VIDEO for both

        Returns:
            True if call was initiated successfully, False otherwise.
            On failure, sets _last_errors[station_id] with error message.
        """
        # Clear any previous error for this station
        self._last_errors[station_id] = None

        # Use lock to prevent concurrent call attempts
        if self._call_lock.locked():
            error_msg = "busy_call_in_progress"
            self._last_errors[station_id] = error_msg
            self.async_set_updated_data(self.data)
            _LOGGER.warning("Another call operation is in progress, ignoring request for station %d", station_id)
            return False

        async with self._call_lock:
            if not self._sip_client:
                error_msg = "not_initialized"
                self._last_errors[station_id] = error_msg
                self.async_set_updated_data(self.data)
                _LOGGER.error("SIP client not initialized")
                return False

            if not self._sip_client.is_registered:
                error_msg = "not_registered"
                self._last_errors[station_id] = error_msg
                self.async_set_updated_data(self.data)
                _LOGGER.error("SIP client not registered")
                return False

            # Warn if connection is unhealthy but try anyway
            if not self._sip_client.is_healthy:
                _LOGGER.warning(
                    "SIP connection unhealthy before call (age=%.0fs), proceeding anyway",
                    self._sip_client.connection_age
                )

            if self._current_call:
                error_msg = "busy_call_in_progress"
                self._last_errors[station_id] = error_msg
                self.async_set_updated_data(self.data)
                _LOGGER.warning("Call already in progress")
                return False

            _LOGGER.info("Initiating call to station %d (mode=%s)", station_id, media_mode.value)

            # Set active_camera BEFORE initiating call because the callback
            # (_on_call_state_changed) may be called during initiate_call
            # and needs _active_camera to be set for video stream setup
            self._active_camera = station_id

            # Use the SIP client to initiate the call
            try:
                call = await self._sip_client.initiate_call(station_id, media_mode)
            except Exception as e:
                error_str = str(e)
                # Check for specific SIP error codes
                if "486" in error_str or "busy" in error_str.lower():
                    error_msg = "busy_gateway"
                elif "408" in error_str or "timeout" in error_str.lower():
                    error_msg = "timeout"
                elif "503" in error_str:
                    error_msg = "service_unavailable"
                else:
                    error_msg = f"error: {error_str[:50]}"

                self._last_errors[station_id] = error_msg
                self._active_camera = None
                self.async_set_updated_data(self.data)
                _LOGGER.warning("Call to station %d failed: %s", station_id, error_msg)
                return False

            if call:
                self._current_call = call
                self._station_states[station_id] = IntercomState.CONNECTED
                # Clear error on success
                self._last_errors[station_id] = None

                # Setup video stream if video mode
                if media_mode in (MediaMode.VIDEO_ONLY, MediaMode.AUDIO_VIDEO):
                    await self._setup_video_stream(call)

                self.async_set_updated_data(self.data)
                return True

            # Call failed - check if sip_client set a specific error
            error_msg = getattr(self._sip_client, 'last_error', None) or "call_failed"
            self._last_errors[station_id] = error_msg
            self._active_camera = None
            self.async_set_updated_data(self.data)
            return False

    async def async_initiate_video_call(self, station_id: int) -> bool:
        """Initiate a video call with audio.

        Uses AUDIO_VIDEO mode to include audio from the start, since BTicino
        gateway doesn't support re-INVITE to add audio mid-session (returns 486).
        """
        return await self.async_initiate_call(station_id, MediaMode.AUDIO_VIDEO)

    async def async_initiate_audio_call(self, station_id: int) -> bool:
        """Initiate an audio-only call for intercom conversation."""
        return await self.async_initiate_call(station_id, MediaMode.AUDIO_ONLY)

    async def async_enable_audio(self) -> bool:
        """Enable bidirectional audio on an existing video call.

        NOTE: BTicino gateway doesn't support re-INVITE (returns 486 Busy).
        The call must be started with AUDIO_VIDEO mode from the beginning.
        This method just starts the SRTP audio handler for an existing session.
        """
        if not self._current_call:
            _LOGGER.error("No active call to add audio to")
            return False

        call = self._current_call

        # Check if we have audio parameters (must be from initial INVITE with AUDIO_VIDEO)
        if not call.audio_rtp_port or not call.remote_audio_port:
            _LOGGER.error("No audio in current session - call was started without audio. "
                         "BTicino doesn't support adding audio via re-INVITE.")
            return False

        if not call.audio_srtp_key_remote:
            _LOGGER.warning("No server audio SRTP key - audio may not work")

        # Stop existing audio handler if any
        if self._audio_handler:
            await self._audio_handler.stop()

        # Create and start bidirectional audio handler
        self._audio_handler = BidirectionalAudio(
            local_port=call.audio_rtp_port,
            remote_host=call.remote_ip or self._config.get(CONF_GATEWAY_ADDRESS, ""),
            remote_port=call.remote_audio_port or call.audio_rtp_port,
            decrypt_key=call.audio_srtp_key_remote,  # Server's key to decrypt incoming
            encrypt_key=call.audio_srtp_key,  # Our key to encrypt outgoing
        )

        await self._audio_handler.start()
        self._audio_enabled = True

        _LOGGER.info("Bidirectional audio enabled: local_port=%d, remote=%s:%d",
                    call.audio_rtp_port,
                    call.remote_ip,
                    call.remote_audio_port or call.audio_rtp_port)

        return True

    async def async_disable_audio(self) -> bool:
        """Disable bidirectional audio."""
        if self._audio_handler:
            await self._audio_handler.stop()
            self._audio_handler = None
        self._audio_enabled = False
        _LOGGER.info("Bidirectional audio disabled")
        return True

    def get_audio_handler(self) -> BidirectionalAudio | None:
        """Get the active audio handler for WebSocket connection."""
        return self._audio_handler

    @property
    def is_audio_enabled(self) -> bool:
        """Return True if audio is enabled."""
        return self._audio_enabled

    @property
    def is_registered(self) -> bool:
        """Return True if SIP client is registered."""
        return self._sip_client is not None and self._sip_client.is_registered

    @property
    def is_sip_healthy(self) -> bool:
        """Return True if SIP connection is healthy (connected, registered, receiving data)."""
        return self._sip_client is not None and self._sip_client.is_healthy

    @property
    def sip_connection_age(self) -> float:
        """Return seconds since last SIP data was received."""
        if self._sip_client:
            return self._sip_client.connection_age
        return float('inf')

    async def async_force_sip_reconnect(self) -> bool:
        """Force a SIP reconnection.

        Useful when the frontend or automations detect issues that the
        automatic health checks haven't caught.

        Returns:
            True if reconnection was initiated, False if no SIP client.
        """
        if not self._sip_client:
            _LOGGER.error("Cannot force reconnect: SIP client not initialized")
            return False

        _LOGGER.info("Forcing SIP reconnection (requested by user/automation)")
        await self._sip_client.force_reconnect()
        return True

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

    def get_last_error(self, station_id: int) -> str | None:
        """Get the last error for a specific station."""
        return self._last_errors.get(station_id)

    def clear_error(self, station_id: int) -> None:
        """Clear the error for a specific station."""
        self._last_errors[station_id] = None

    def get_rtsp_url(self, camera_id: int) -> str | None:
        """Get RTSP URL for a camera."""
        return self._rtsp_urls.get(camera_id)

    def get_webrtc_url(self, camera_id: int) -> str | None:
        """Get WebRTC URL for a camera."""
        return self._webrtc_urls.get(camera_id)

    def get_hls_available(self, camera_id: int) -> bool:
        """Get HLS availability for a camera (legacy, use get_stream_available)."""
        return self._stream_available.get(camera_id, False)

    def set_hls_available(self, camera_id: int, available: bool) -> None:
        """Set HLS availability for a camera (legacy, use set_stream_available)."""
        self._stream_available[camera_id] = available

    def get_stream_available(self, camera_id: int) -> bool:
        """Get stream availability for a camera (WebRTC or HLS)."""
        return self._stream_available.get(camera_id, False)

    def set_stream_available(self, camera_id: int, available: bool) -> None:
        """Set stream availability for a camera."""
        self._stream_available[camera_id] = available

    def is_webrtc_mode(self) -> bool:
        """Check if WebRTC mode is active."""
        return self._media_proxy is not None and self._media_proxy.is_webrtc_mode()

    def get_go2rtc_api_url(self) -> str:
        """Get go2rtc API base URL for WebRTC signaling."""
        if self._media_proxy:
            return self._media_proxy.get_go2rtc_api_url()
        return ""

    def get_webrtc_stream_id(self, camera_id: int) -> str:
        """Get WebRTC stream ID for a camera."""
        if self._media_proxy:
            return self._media_proxy.get_stream_name(camera_id)
        return ""

    async def webrtc_offer(self, camera_id: int, sdp_offer: str) -> str | None:
        """Process WebRTC offer and return SDP answer.

        This is the signaling endpoint for frontend WebRTC connections.

        Args:
            camera_id: Camera identifier
            sdp_offer: SDP offer from browser

        Returns:
            SDP answer from go2rtc, or None on error
        """
        if not self._media_proxy:
            _LOGGER.error("Media proxy not initialized")
            return None

        return await self._media_proxy.webrtc_offer(camera_id, sdp_offer)
