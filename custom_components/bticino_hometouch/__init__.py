"""BTicino Door Entry Intercom integration for Home Assistant."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.components.frontend import async_register_built_in_panel
from homeassistant.components.http import StaticPathConfig

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

    # Register frontend card
    await _register_frontend_card(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start SIP client
    await coordinator.async_start_sip()

    # Register audio WebSocket endpoint
    from .audio_websocket import setup_audio_websocket
    setup_audio_websocket(hass, coordinator)

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
