"""Config flow for the Ampio integration."""

import logging
from typing import Any, override

from ampio_mqtt import (
    AmpioAuthError,
    AmpioClient,
    AmpioConnectionError,
    AmpioServerInfo,
)
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from .const import DEFAULT_HOST, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class AmpioConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ampio."""

    async def _async_check(
        self, user_input: dict[str, Any]
    ) -> tuple[AmpioServerInfo | None, dict[str, str]]:
        """Validate one set of credentials, and name the failure.

        Returns the server identity and no errors, or None and the one
        error key the form shows. ``AmpioTimeoutError`` subclasses
        ``AmpioConnectionError``, so a slow broker and a transport failure
        read alike: something is wrong with the connection, not the
        account.
        """
        try:
            info = await AmpioClient.check_connection(
                user_input[CONF_HOST],
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
        except AmpioAuthError:
            return None, {"base": "invalid_auth"}
        except AmpioConnectionError:
            return None, {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected exception")
            return None, {"base": "unknown"}
        else:
            return info, {}

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            info, errors = await self._async_check(user_input)
            if info is not None:
                await self.async_set_unique_id(info.server_key)
                self._abort_if_unique_id_configured(updates=user_input)
                return self.async_create_entry(
                    title=user_input[CONF_HOST], data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input or {CONF_HOST: DEFAULT_HOST}
            ),
            errors=errors,
        )
