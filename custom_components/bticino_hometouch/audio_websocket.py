"""WebSocket handler for bidirectional audio streaming.

This module provides a WebSocket endpoint for the browser to:
1. Receive decoded audio from the intercom (G.711 A-law -> PCM)
2. Send microphone audio to the intercom (PCM -> G.711 A-law)

The WebSocket carries binary audio data in both directions.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from aiohttp import web, WSMsgType
from homeassistant.components.http import HomeAssistantView

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from .coordinator import BticinoCoordinator

_LOGGER = logging.getLogger(__name__)


class AudioWebSocketView(HomeAssistantView):
    """WebSocket endpoint for bidirectional audio.

    URL: /api/bticino_hometouch/audio_ws

    Authentication: Token passed via query string parameter 'token'.
    This is required because WebSocket API doesn't support Authorization headers.
    """

    url = "/api/bticino_hometouch/audio_ws"
    name = "api:bticino_hometouch:audio_ws"
    requires_auth = False  # We handle auth manually via query param

    def __init__(self, hass: HomeAssistant, coordinator: BticinoCoordinator):
        """Initialize the WebSocket view."""
        self._hass = hass
        self._coordinator = coordinator
        self._active_connections: list[web.WebSocketResponse] = []

    async def get(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket connection."""
        # Validate auth token from query string
        token = request.query.get("token")
        if not token:
            _LOGGER.warning("Audio WebSocket: No auth token provided")
            return web.Response(status=401, text="Missing auth token")

        # Validate the token using HA's auth system
        try:
            # Note: async_validate_access_token may or may not be async depending on HA version
            result = self._hass.auth.async_validate_access_token(token)
            # Handle both async and sync versions
            if asyncio.iscoroutine(result):
                refresh_token = await result
            else:
                refresh_token = result

            if refresh_token is None:
                _LOGGER.warning("Audio WebSocket: Invalid auth token")
                return web.Response(status=401, text="Invalid auth token")

            user_name = refresh_token.user.name if refresh_token.user else "unknown"
            _LOGGER.debug("Audio WebSocket: Auth validated for user %s", user_name)
        except Exception as e:
            _LOGGER.error("Audio WebSocket: Auth validation error: %s", e)
            return web.Response(status=401, text="Auth validation failed")

        ws = web.WebSocketResponse()
        await ws.prepare(request)

        _LOGGER.info("Audio WebSocket connected (user: %s)", user_name)
        self._active_connections.append(ws)

        # Get the audio handler from coordinator
        audio_handler = self._coordinator.get_audio_handler()

        if not audio_handler:
            _LOGGER.warning("No active audio handler - call not in progress?")
            await ws.send_json({"error": "no_active_call"})
            await ws.close()
            return ws

        # Set up callback to forward audio to this WebSocket
        def on_audio_received(audio_data: bytes):
            """Forward audio from intercom to browser."""
            if not ws.closed:
                asyncio.create_task(self._send_audio_to_ws(ws, audio_data))

        # Register callback with audio handler
        audio_handler._on_audio_received = on_audio_received

        # Send ready message
        await ws.send_json({"status": "connected", "sample_rate": 8000, "codec": "pcma"})

        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    # Audio data from browser microphone
                    if audio_handler and audio_handler.is_running:
                        # Browser sends PCM data, we need to convert to G.711 A-law
                        # For now, assume browser sends A-law encoded data
                        await audio_handler.send_audio(msg.data)
                elif msg.type == WSMsgType.TEXT:
                    # Control messages
                    try:
                        import json
                        data = json.loads(msg.data)
                        await self._handle_control_message(ws, data, audio_handler)
                    except Exception as e:
                        _LOGGER.debug("Invalid control message: %s", e)
                elif msg.type == WSMsgType.ERROR:
                    _LOGGER.error("WebSocket error: %s", ws.exception())
                    break
        except Exception as e:
            _LOGGER.error("WebSocket handler error: %s", e)
        finally:
            self._active_connections.remove(ws)
            _LOGGER.info("Audio WebSocket disconnected")

        return ws

    async def _send_audio_to_ws(self, ws: web.WebSocketResponse, audio_data: bytes):
        """Send audio data to WebSocket."""
        try:
            if not ws.closed:
                await ws.send_bytes(audio_data)
        except Exception as e:
            _LOGGER.debug("Failed to send audio to WS: %s", e)

    async def _handle_control_message(self, ws, data: dict, audio_handler):
        """Handle control messages from browser."""
        msg_type = data.get("type")

        if msg_type == "mute":
            # Mute outgoing audio
            _LOGGER.info("Audio muted by user")
            await ws.send_json({"status": "muted"})
        elif msg_type == "unmute":
            _LOGGER.info("Audio unmuted by user")
            await ws.send_json({"status": "unmuted"})
        elif msg_type == "ping":
            await ws.send_json({"type": "pong"})

    async def broadcast_audio(self, audio_data: bytes):
        """Broadcast audio to all connected WebSockets."""
        for ws in self._active_connections:
            if not ws.closed:
                try:
                    await ws.send_bytes(audio_data)
                except Exception:
                    pass


def setup_audio_websocket(hass: HomeAssistant, coordinator: BticinoCoordinator):
    """Register the audio WebSocket endpoint."""
    hass.http.register_view(AudioWebSocketView(hass, coordinator))
    _LOGGER.info("Audio WebSocket endpoint registered at /api/bticino_hometouch/audio_ws")
