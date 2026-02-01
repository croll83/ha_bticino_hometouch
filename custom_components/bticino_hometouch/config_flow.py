"""Config flow for BTicino Hometouch integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    DOMAIN,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_GATEWAY_MAC,
    CONF_NUM_CAMERAS,
    CONF_NUM_LOCKS,
    CONF_SIP_SERVER,
    CONF_SIP_PORT,
    CONF_SIP_USERNAME,
    CONF_SIP_PASSWORD,
    CONF_SIP_DOMAIN,
    CONF_SIP_ACCOUNT,
    CONF_GATEWAY_ADDRESS,
    CONF_CLIENT_CERT,
    CONF_CLIENT_KEY,
    CONF_CA_CERT,
    CONF_PLANT_ID,
    CONF_GATEWAY_ID,
    CONF_DEVICE_ID,
    CONF_CERT_EXPIRY,
    CONF_LOCK_COMMANDS,
    DEFAULT_SIP_SERVER,
    DEFAULT_SIP_PORT,
    DEFAULT_NUM_CAMERAS,
    DEFAULT_NUM_LOCKS,
)
from .bticino_api import BticinoApi, BticinoAuthError, BticinoProvisioningError

_LOGGER = logging.getLogger(__name__)


# Simple setup schema - just email, password, MAC, and device counts
# Using NumberSelector with BOX mode for text input instead of slider
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL)
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_GATEWAY_MAC): TextSelector(),
        vol.Required(CONF_NUM_CAMERAS, default=DEFAULT_NUM_CAMERAS): NumberSelector(
            NumberSelectorConfig(min=1, max=10, step=1, mode=NumberSelectorMode.BOX)
        ),
        vol.Required(CONF_NUM_LOCKS, default=DEFAULT_NUM_LOCKS): NumberSelector(
            NumberSelectorConfig(min=1, max=10, step=1, mode=NumberSelectorMode.BOX)
        ),
    }
)


async def validate_and_provision(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, Any]:
    """Validate credentials and provision the device."""
    email = data[CONF_EMAIL]
    password = data[CONF_PASSWORD]

    _LOGGER.info("Starting BTicino provisioning for %s", email)

    async with BticinoApi(email, password) as api:
        try:
            result = await api.provision_device(
                gateway_mac=data.get(CONF_GATEWAY_MAC),
                device_name="HomeAssistant",
            )
        except BticinoAuthError as err:
            _LOGGER.error("Authentication failed: %s", err)
            raise InvalidAuth from err
        except BticinoProvisioningError as err:
            _LOGGER.error("Provisioning failed: %s", err)
            raise CannotConnect from err

    _LOGGER.info(
        "Provisioning successful. Plant: %s, Gateway: %s, SIP: %s",
        result.plant_id,
        result.gateway_id,
        result.sip_account,
    )

    # Convert to int (NumberSelector returns float)
    num_cameras = int(data[CONF_NUM_CAMERAS])
    num_locks = int(data[CONF_NUM_LOCKS])

    # Return full configuration
    return {
        # User input (stored for re-provisioning/renewal)
        CONF_EMAIL: email,
        CONF_PASSWORD: password,
        CONF_GATEWAY_MAC: data.get(CONF_GATEWAY_MAC, ""),
        CONF_NUM_CAMERAS: num_cameras,
        CONF_NUM_LOCKS: num_locks,
        # Provisioned data
        CONF_PLANT_ID: result.plant_id,
        CONF_GATEWAY_ID: result.gateway_id,
        CONF_SIP_ACCOUNT: result.sip_account,
        CONF_DEVICE_ID: result.device_id,
        # SIP configuration
        CONF_SIP_SERVER: DEFAULT_SIP_SERVER,
        CONF_SIP_PORT: DEFAULT_SIP_PORT,
        CONF_SIP_USERNAME: result.sip_username,
        CONF_SIP_PASSWORD: result.sip_password,
        CONF_SIP_DOMAIN: result.sip_domain,
        # Certificates
        CONF_CLIENT_CERT: result.client_cert,
        CONF_CLIENT_KEY: result.client_key,
        CONF_CA_CERT: result.ca_cert,
        CONF_CERT_EXPIRY: result.cert_expiry.isoformat(),
        # Lock commands (default to type A)
        CONF_LOCK_COMMANDS: ["A"] * num_locks,
        # Gateway address placeholder (will be discovered or set later)
        CONF_GATEWAY_ADDRESS: data.get(CONF_GATEWAY_MAC, ""),
    }


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BTicino Hometouch."""

    VERSION = 2  # Incremented version for new config format

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - simple setup with email, password, MAC."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Check if already configured with this email
            await self.async_set_unique_id(user_input[CONF_EMAIL].lower())
            self._abort_if_unique_id_configured()

            try:
                # Validate and provision
                self._data = await validate_and_provision(self.hass, user_input)

            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception during provisioning")
                errors["base"] = "unknown"
            else:
                # Success - create entry
                return self.async_create_entry(
                    title=f"BTicino ({self._data[CONF_SIP_DOMAIN]})",
                    data=self._data,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for BTicino Hometouch."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            # Update config entry data
            new_data = {**self.config_entry.data}
            new_data[CONF_NUM_CAMERAS] = user_input[CONF_NUM_CAMERAS]
            new_data[CONF_NUM_LOCKS] = user_input[CONF_NUM_LOCKS]

            # Adjust lock commands array size
            current_commands = new_data.get(CONF_LOCK_COMMANDS, [])
            new_num_locks = user_input[CONF_NUM_LOCKS]
            if len(current_commands) < new_num_locks:
                # Extend with type A
                current_commands.extend(
                    ["A"] * (new_num_locks - len(current_commands))
                )
            elif len(current_commands) > new_num_locks:
                # Truncate
                current_commands = current_commands[:new_num_locks]
            new_data[CONF_LOCK_COMMANDS] = current_commands

            # Update gateway address if provided
            if user_input.get(CONF_GATEWAY_ADDRESS):
                new_data[CONF_GATEWAY_ADDRESS] = user_input[CONF_GATEWAY_ADDRESS]

            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data
            )
            return self.async_create_entry(title="", data={})

        # Show current settings with BOX mode for number inputs
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NUM_CAMERAS,
                        default=self.config_entry.data.get(
                            CONF_NUM_CAMERAS, DEFAULT_NUM_CAMERAS
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(min=1, max=10, step=1, mode=NumberSelectorMode.BOX)
                    ),
                    vol.Required(
                        CONF_NUM_LOCKS,
                        default=self.config_entry.data.get(
                            CONF_NUM_LOCKS, DEFAULT_NUM_LOCKS
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(min=1, max=10, step=1, mode=NumberSelectorMode.BOX)
                    ),
                    vol.Optional(
                        CONF_GATEWAY_ADDRESS,
                        default=self.config_entry.data.get(CONF_GATEWAY_ADDRESS, ""),
                    ): TextSelector(),
                }
            ),
        )


class InvalidAuth(HomeAssistantError):
    """Error to indicate invalid authentication."""


class CannotConnect(HomeAssistantError):
    """Error to indicate connection/provisioning failed."""
