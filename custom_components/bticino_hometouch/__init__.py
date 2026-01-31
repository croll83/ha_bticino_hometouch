"""BTicino Door Entry Intercom integration for Home Assistant."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BTicino Hometouch from a config entry."""
    coordinator = BticinoCoordinator(hass, entry)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start SIP client
    await coordinator.async_start_sip()

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
