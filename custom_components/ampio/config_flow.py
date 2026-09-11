"""Config flow for the Ampio integration."""

from collections.abc import Mapping
import logging
from typing import Any, override

from ampio_mqtt import (
    AmpioAuthError,
    AmpioClient,
    AmpioConnectionError,
    AmpioServerInfo,
)
import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
)
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

    # Carried from the credentials step to the server confirmation.
    _pending_input: dict[str, Any]
    _pending_key: str
    _pending_mac: int

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

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Answer the credential rejection the running entry reported."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take the credentials to use after a rejection."""
        return await self._async_step_credentials(user_input, "reauth_confirm")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take a new address or a new account for the entry."""
        return await self._async_step_credentials(user_input, "reconfigure")

    def _entry_for_source(self) -> ConfigEntry:
        """The entry this flow belongs to, per its source.

        Both core helpers raise when the source does not match, so the
        branch is what keeps them apart.
        """
        if self.source == SOURCE_REAUTH:
            return self._get_reauth_entry()
        return self._get_reconfigure_entry()

    async def _async_step_credentials(
        self, user_input: dict[str, Any] | None, step_id: str
    ) -> ConfigFlowResult:
        """Point the entry at a host and an account, from one shared form.

        The reauth dialog and the reconfigure dialog differ in their step
        id alone, which is what gives each its own title and description.
        The entry id does not move, so the reload keeps every device
        record and every entity record.
        """
        entry = self._entry_for_source()
        errors: dict[str, str] = {}
        if user_input is not None:
            info, errors = await self._async_check(user_input)
            if info is not None:
                if entry.unique_id is not None and entry.unique_id != info.server_key:
                    self._pending_input = user_input
                    self._pending_key = info.server_key
                    self._pending_mac = info.mac
                    return await self.async_step_confirm_server()
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=info.server_key,
                    data_updates=user_input,
                    title=user_input[CONF_HOST],
                )

        # The first form offers what the entry holds, minus the password.
        # A retry after an error offers what the user submitted, as the
        # user step does.
        suggested = user_input or {
            CONF_HOST: entry.data[CONF_HOST],
            CONF_USERNAME: entry.data[CONF_USERNAME],
        }
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, suggested
            ),
            errors=errors,
        )

    async def async_step_confirm_server(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a re-key before the entry follows another M-SERV.

        The entry's identities carry no server mac, so the records all
        survive and bind to the objects of the new server's Designer
        project. The step names the new mac alone: the stored one is only
        held as ``server_key``, whose format must not be parsed.
        """
        if user_input is None:
            return self.async_show_form(
                step_id="confirm_server",
                data_schema=vol.Schema({}),
                description_placeholders={
                    "host": self._pending_input[CONF_HOST],
                    "mac": f"0x{self._pending_mac:X}",
                },
            )
        return self.async_update_reload_and_abort(
            self._entry_for_source(),
            unique_id=self._pending_key,
            data_updates=self._pending_input,
            title=self._pending_input[CONF_HOST],
        )
