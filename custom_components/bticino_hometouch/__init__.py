"""BTicino Door Entry Intercom integration for Home Assistant."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from pathlib import Path

import aiohttp
from aiohttp import web

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.components.frontend import async_register_built_in_panel
from homeassistant.components.http import StaticPathConfig, HomeAssistantView

from .const import (
    DOMAIN,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SIP_ACCOUNT,
    CONF_CLIENT_CERT,
    CONF_CLIENT_KEY,
    CONF_CA_CERT,
    CONF_CERT_EXPIRY,
    CERT_RENEWAL_DAYS_BEFORE_EXPIRY,
)
from .coordinator import BticinoCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.BINARY_SENSOR,
]

# Check certificate expiry daily
CERT_CHECK_INTERVAL = timedelta(days=1)

# Frontend card file
CARD_FILE = "bticino-intercom-card.js"
CARD_URL = f"/bticino_hometouch/{CARD_FILE}"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BTicino Hometouch from a config entry."""
    coordinator = BticinoCoordinator(hass, entry)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Configure go2rtc streams for WebRTC
    await _configure_go2rtc_streams(hass)

    # Register frontend card
    await _register_frontend_card(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start SIP client
    await coordinator.async_start_sip()

    # Register audio WebSocket endpoint
    from .audio_websocket import setup_audio_websocket
    setup_audio_websocket(hass, coordinator)

    # Register WebRTC proxy view for HTTPS support
    hass.http.register_view(Go2RTCWebRTCProxyView())

    # Schedule certificate renewal check
    async def check_certificate_renewal(now: datetime) -> None:
        """Check if certificate needs renewal."""
        await _check_and_renew_certificate(hass, entry)

    # Run initial check
    hass.async_create_task(check_certificate_renewal(datetime.now(timezone.utc)))

    # Schedule daily checks
    entry.async_on_unload(
        async_track_time_interval(
            hass,
            check_certificate_renewal,
            CERT_CHECK_INTERVAL,
        )
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: BticinoCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Stop SIP client
    await coordinator.async_stop_sip()

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def _check_and_renew_certificate(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Check certificate expiry and renew if needed."""
    cert_expiry_str = entry.data.get(CONF_CERT_EXPIRY)
    if not cert_expiry_str:
        _LOGGER.debug("No certificate expiry date stored, skipping renewal check")
        return

    try:
        cert_expiry = datetime.fromisoformat(cert_expiry_str)
        # Ensure timezone-aware
        if cert_expiry.tzinfo is None:
            cert_expiry = cert_expiry.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError) as err:
        _LOGGER.error("Invalid certificate expiry date: %s", err)
        return

    now = datetime.now(timezone.utc)
    days_until_expiry = (cert_expiry - now).days

    _LOGGER.debug(
        "Certificate expires on %s (%d days remaining)",
        cert_expiry.isoformat(),
        days_until_expiry,
    )

    if days_until_expiry > CERT_RENEWAL_DAYS_BEFORE_EXPIRY:
        # Certificate still valid, no renewal needed
        return

    _LOGGER.info(
        "Certificate expires in %d days, initiating automatic renewal",
        days_until_expiry,
    )

    # Get credentials from config
    email = entry.data.get(CONF_EMAIL)
    password = entry.data.get(CONF_PASSWORD)
    sip_account = entry.data.get(CONF_SIP_ACCOUNT)

    if not all([email, password, sip_account]):
        _LOGGER.error("Missing credentials for certificate renewal")
        return

    try:
        from .bticino_api import BticinoApi

        async with BticinoApi(email, password) as api:
            client_cert, client_key, ca_cert, new_expiry = await api.renew_certificate(
                sip_account
            )

        # Update config entry with new certificates
        new_data = {**entry.data}
        new_data[CONF_CLIENT_CERT] = client_cert
        new_data[CONF_CLIENT_KEY] = client_key
        new_data[CONF_CA_CERT] = ca_cert
        new_data[CONF_CERT_EXPIRY] = new_expiry.isoformat()

        hass.config_entries.async_update_entry(entry, data=new_data)

        _LOGGER.info(
            "Certificate renewed successfully, new expiry: %s",
            new_expiry.isoformat(),
        )

        # Restart SIP client to use new certificates
        coordinator: BticinoCoordinator = hass.data[DOMAIN].get(entry.entry_id)
        if coordinator:
            _LOGGER.info("Restarting SIP client with new certificates")
            await coordinator.async_stop_sip()
            await coordinator.async_start_sip()

    except Exception as err:
        _LOGGER.error("Failed to renew certificate: %s", err)


async def _configure_go2rtc_streams(hass: HomeAssistant) -> None:
    """Configure go2rtc.yaml with BTicino streams for WebRTC support.

    go2rtc requires streams to be pre-configured in YAML to accept RTSP push.
    This function adds bticino_live_1 through bticino_live_10 empty streams.
    """
    import yaml
    import asyncio

    go2rtc_config_path = Path("/config/go2rtc.yaml")

    # Check if already configured
    if DOMAIN in hass.data and "go2rtc_configured" in hass.data[DOMAIN]:
        return

    def _update_go2rtc_config():
        """Update go2rtc.yaml with BTicino streams (runs in executor)."""
        # Default config if file doesn't exist
        config = {
            "api": {"listen": ":1984", "allow_origin": "*"},
            "rtsp": {"listen": ":8554"},
            "webrtc": {
                "listen": ":8555",
                "candidates": ["stun:stun.l.google.com:19302"]
            },
            "ffmpeg": {"bin": "ffmpeg"},
            "streams": {}
        }

        # Read existing config if present
        if go2rtc_config_path.exists():
            try:
                with open(go2rtc_config_path, 'r') as f:
                    existing = yaml.safe_load(f)
                    if existing:
                        config = existing
            except Exception as e:
                _LOGGER.warning("Could not read go2rtc.yaml: %s", e)

        # Ensure streams section exists
        if "streams" not in config or config["streams"] is None:
            config["streams"] = {}

        # Add BTicino streams if not present
        streams_added = False
        for i in range(1, 11):  # bticino_live_1 through bticino_live_10
            stream_name = f"bticino_live_{i}"
            if stream_name not in config["streams"]:
                config["streams"][stream_name] = []
                streams_added = True

        if streams_added:
            # Write updated config
            try:
                with open(go2rtc_config_path, 'w') as f:
                    yaml.dump(config, f, default_flow_style=False, sort_keys=False)
                return True
            except Exception as e:
                _LOGGER.error("Could not write go2rtc.yaml: %s", e)
                return False

        return False  # No changes needed

    try:
        config_changed = await asyncio.to_thread(_update_go2rtc_config)

        if config_changed:
            _LOGGER.info("Added BTicino streams to go2rtc.yaml (bticino_live_1 to bticino_live_10)")
            _LOGGER.warning(
                "go2rtc.yaml was updated. Please restart the go2rtc add-on or Home Assistant "
                "for changes to take effect."
            )
        else:
            _LOGGER.debug("go2rtc.yaml already configured with BTicino streams")

        # Mark as configured
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN]["go2rtc_configured"] = True

    except Exception as e:
        _LOGGER.error("Failed to configure go2rtc streams: %s", e)


async def _register_frontend_card(hass: HomeAssistant) -> None:
    """Register the BTicino Intercom custom card."""
    # Check if already registered
    if DOMAIN in hass.data and "frontend_registered" in hass.data[DOMAIN]:
        return

    # Path to the www folder in this integration
    www_path = Path(__file__).parent / "www"

    if not www_path.exists():
        _LOGGER.warning("Frontend www folder not found: %s", www_path)
        return

    card_file = www_path / CARD_FILE
    if not card_file.exists():
        _LOGGER.warning("Frontend card file not found: %s", card_file)
        return

    # Copy card to /config/www (this is the most reliable method)
    dest_path = Path("/config/www") / CARD_FILE
    try:
        import shutil
        import asyncio

        def _copy_card():
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(card_file, dest_path)

        # Use asyncio.to_thread to avoid blocking the event loop
        await asyncio.to_thread(_copy_card)
        _LOGGER.info("Installed BTicino Intercom card to %s", dest_path)
    except Exception as e:
        _LOGGER.warning("Could not copy card to /config/www: %s", e)
        return

    # The card is now available at /local/bticino-intercom-card.js
    local_url = f"/local/{CARD_FILE}"

    # Try to add to Lovelace resources automatically
    try:
        if "lovelace" in hass.data:
            lovelace_data = hass.data["lovelace"]
            if hasattr(lovelace_data, "resources"):
                resources = lovelace_data.resources
                # Check if resource already exists
                existing = [r for r in resources.async_items() if CARD_FILE in r.get("url", "")]
                if not existing:
                    await resources.async_create_item({
                        "url": local_url,
                        "type": "module"
                    })
                    _LOGGER.info("Added BTicino Intercom card to Lovelace resources: %s", local_url)
    except Exception as e:
        _LOGGER.debug("Could not auto-add Lovelace resource (manual add may be needed): %s", e)

    # Mark as registered
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["frontend_registered"] = True


class Go2RTCWebRTCProxyView(HomeAssistantView):
    """Proxy WebRTC requests to go2rtc to allow HTTPS access."""

    url = "/api/bticino_hometouch/webrtc/{stream_name}"
    name = "api:bticino_hometouch:webrtc"
    requires_auth = False  # Allow unauthenticated for WebRTC signaling

    async def post(self, request: web.Request, stream_name: str) -> web.Response:
        """Handle WebRTC offer and proxy to go2rtc."""
        try:
            # Get the offer from request body
            body = await request.json()

            # Forward to go2rtc
            go2rtc_url = f"http://127.0.0.1:1984/api/webrtc?src={stream_name}"

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    go2rtc_url,
                    json=body,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        answer = await resp.json()
                        return web.json_response(answer)
                    else:
                        error_text = await resp.text()
                        _LOGGER.error("go2rtc error: %d %s", resp.status, error_text)
                        return web.json_response(
                            {"error": f"go2rtc error: {resp.status}"},
                            status=resp.status
                        )
        except Exception as e:
            _LOGGER.error("WebRTC proxy error: %s", e)
            return web.json_response({"error": str(e)}, status=500)
