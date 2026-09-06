"""Tests for the Ampio config flow."""

from unittest.mock import MagicMock

from ampio_mqtt import AmpioAuthError, AmpioConnectionError, AmpioTimeoutError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    get_schema_suggested_value,
)

from custom_components.ampio.const import DOMAIN
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import MSERV_MAC, USER_INPUT

pytestmark = pytest.mark.usefixtures("mock_setup_entry")


@pytest.mark.usefixtures("mock_client_class")
async def test_user_flow_success(hass: HomeAssistant) -> None:
    """A valid connection creates the entry with the server mac as unique_id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    # The host field defaults to the well-known `ampio.local`.
    assert (
        get_schema_suggested_value(result["data_schema"].schema, CONF_HOST)
        == "ampio.local"
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == USER_INPUT[CONF_HOST]
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == MSERV_MAC


@pytest.mark.parametrize(
    ("side_effect", "expected_error"),
    [
        pytest.param(
            AmpioConnectionError("boom"), "cannot_connect", id="cannot_connect"
        ),
        pytest.param(AmpioAuthError("bad creds"), "invalid_auth", id="invalid_auth"),
        # A slow broker and an identity-less info reply both raise the retryable
        # timeout: a connection problem, not an account problem.
        pytest.param(
            AmpioTimeoutError("no usable info reply"),
            "cannot_connect",
            id="info_timeout",
        ),
        # An unexpected error must re-show the form, not crash the flow.
        pytest.param(ValueError("username is required"), "unknown", id="unknown"),
    ],
)
async def test_user_flow_errors_and_recovers(
    hass: HomeAssistant,
    mock_client_class: MagicMock,
    side_effect: BaseException,
    expected_error: str,
) -> None:
    """Each error shape stays on the user form; a valid retry creates the entry."""
    mock_client_class.check_connection.side_effect = side_effect

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}
    # The re-shown form carries the submitted input, not the default host.
    assert (
        get_schema_suggested_value(result["data_schema"].schema, CONF_HOST)
        == USER_INPUT[CONF_HOST]
    )

    mock_client_class.check_connection.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_client_class")
async def test_second_entry_is_refused(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: MagicMock,
) -> None:
    """One M-SERV per Home Assistant: a second flow aborts before its form.

    Object ids are unique per server only, and no identity carries the
    server mac, so a second entry would collide on every unique id.
    """
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
    assert dict(mock_config_entry.data) == USER_INPUT
    assert mock_setup_entry.call_count == 1
