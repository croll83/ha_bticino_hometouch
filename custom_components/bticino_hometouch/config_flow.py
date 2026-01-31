"""Config flow for BTicino Hometouch integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

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
    DEFAULT_SIP_PORT,
    DEFAULT_NUM_CAMERAS,
    DEFAULT_NUM_LOCKS,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SIP_SERVER, default="sipserver.bs.iotleg.com"): str,
        vol.Required(CONF_SIP_PORT, default=DEFAULT_SIP_PORT): int,
        vol.Required(CONF_SIP_USERNAME): str,
        vol.Required(CONF_SIP_PASSWORD): str,
        vol.Required(CONF_SIP_DOMAIN): str,
        vol.Required(CONF_GATEWAY_ADDRESS): str,
        vol.Required(CONF_NUM_CAMERAS, default=DEFAULT_NUM_CAMERAS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=10)
        ),
        vol.Required(CONF_NUM_LOCKS, default=DEFAULT_NUM_LOCKS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=10)
        ),
    }
)

STEP_CERTS_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_CERT): str,
        vol.Required(CONF_CLIENT_KEY): str,
        vol.Required(CONF_CA_CERT): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    # Basic validation
    if not data[CONF_SIP_USERNAME]:
        raise InvalidAuth("Username is required")

    if not data[CONF_SIP_PASSWORD]:
        raise InvalidAuth("Password is required")

    # Return info to store in the config entry
    return {"title": f"BTicino Hometouch ({data[CONF_SIP_DOMAIN]})"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BTicino Hometouch."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                self._data = user_input
                return await self.async_step_certificates()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "example_username": "user@email.com-MACADDRESS@123456.bs.iotleg.com",
                "example_domain": "123456.bs.iotleg.com",
                "example_gateway": "SERIALNUMBER.bs.iotleg.com",
            },
        )

    async def async_step_certificates(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the certificates step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate certificates
            try:
                if "-----BEGIN CERTIFICATE-----" not in user_input[CONF_CLIENT_CERT]:
                    raise InvalidCert("Invalid client certificate format")

                if "-----BEGIN" not in user_input[CONF_CLIENT_KEY]:
                    raise InvalidCert("Invalid private key format")

                if "-----BEGIN CERTIFICATE-----" not in user_input[CONF_CA_CERT]:
                    raise InvalidCert("Invalid CA certificate format")

            except InvalidCert as e:
                errors["base"] = "invalid_cert"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                # Merge data and create entry
                self._data.update(user_input)

                # Default lock commands (all type A)
                self._data[CONF_LOCK_COMMANDS] = ["A"] * self._data[CONF_NUM_LOCKS]

                return self.async_create_entry(
                    title=f"BTicino Hometouch ({self._data[CONF_SIP_DOMAIN]})",
                    data=self._data,
                )

        return self.async_show_form(
            step_id="certificates",
            data_schema=STEP_CERTS_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "cert_info": "Paste the contents of your decrypted certificate files",
            },
        )


class InvalidAuth(HomeAssistantError):
    """Error to indicate invalid authentication."""


class InvalidCert(HomeAssistantError):
    """Error to indicate invalid certificate."""
