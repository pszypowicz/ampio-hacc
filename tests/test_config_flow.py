"""Tests for the Ampio config flow."""

from unittest.mock import MagicMock

from ampio_mqtt import AmpioAuthError, AmpioConnectionError, AmpioTimeoutError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    get_schema_suggested_value,
)

from custom_components.ampio.const import DOMAIN
from homeassistant.config_entries import SOURCE_USER, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import MSERV_MAC, OTHER_MSERV_MAC, OTHER_SERVER_INFO, USER_INPUT

pytestmark = pytest.mark.usefixtures("mock_setup_entry")

# The four failure shapes the flow maps to a form error. A slow broker and
# an identity-less info reply both raise the retryable timeout, which is a
# connection problem rather than an account problem. An unexpected error
# must re-show the form rather than crash the flow.
FLOW_ERRORS = [
    pytest.param(AmpioConnectionError("boom"), "cannot_connect", id="cannot_connect"),
    pytest.param(AmpioAuthError("bad creds"), "invalid_auth", id="invalid_auth"),
    pytest.param(
        AmpioTimeoutError("no usable info reply"), "cannot_connect", id="info_timeout"
    ),
    pytest.param(ValueError("username is required"), "unknown", id="unknown"),
]


async def _start_credentials_flow(
    hass: HomeAssistant, entry: MockConfigEntry, source: str
) -> ConfigFlowResult:
    """Open the reauth or the reconfigure flow for an entry."""
    if source == "reauth":
        return await entry.start_reauth_flow(hass)
    return await entry.start_reconfigure_flow(hass)


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


@pytest.mark.parametrize(("side_effect", "expected_error"), FLOW_ERRORS)
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


@pytest.mark.usefixtures("mock_client_class")
async def test_reauth_flow_replaces_the_credentials(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A rejected password is replaced in place, and the entry keeps its key."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reauth_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    # Core fills this from the entry title when the source is reauth, so the
    # dialog names the server without the flow passing anything.
    assert result["description_placeholders"] == {"name": mock_config_entry.title}
    schema = result["data_schema"].schema
    assert get_schema_suggested_value(schema, CONF_HOST) == USER_INPUT[CONF_HOST]
    assert (
        get_schema_suggested_value(schema, CONF_USERNAME) == USER_INPUT[CONF_USERNAME]
    )
    # The stored password is never handed back to the form.
    assert get_schema_suggested_value(schema, CONF_PASSWORD) is None

    rotated = {**USER_INPUT, CONF_PASSWORD: "rotated"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], rotated)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert dict(mock_config_entry.data) == rotated
    assert mock_config_entry.unique_id == MSERV_MAC


@pytest.mark.usefixtures("mock_client_class")
async def test_reconfigure_flow_moves_the_host_and_the_title(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A new address and account are written, and the entry title follows."""
    mock_config_entry.add_to_hass(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    # The reconfigure description carries no placeholder, and core fills one
    # for the reauth source alone.
    assert result["description_placeholders"] is None

    moved = {**USER_INPUT, CONF_HOST: "ampio2.test", CONF_USERNAME: "admin"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], moved)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert dict(mock_config_entry.data) == moved
    assert mock_config_entry.title == "ampio2.test"
    assert mock_config_entry.unique_id == MSERV_MAC


@pytest.mark.parametrize(
    ("source", "step_id"),
    [("reauth", "reauth_confirm"), ("reconfigure", "reconfigure")],
    ids=["reauth", "reconfigure"],
)
@pytest.mark.parametrize(("side_effect", "expected_error"), FLOW_ERRORS)
async def test_credentials_flow_errors_and_recovers(
    hass: HomeAssistant,
    mock_client_class: MagicMock,
    mock_config_entry: MockConfigEntry,
    side_effect: BaseException,
    expected_error: str,
    source: str,
    step_id: str,
) -> None:
    """Each error shape stays on its own step; a valid retry finishes."""
    mock_config_entry.add_to_hass(hass)
    mock_client_class.check_connection.side_effect = side_effect

    result = await _start_credentials_flow(hass, mock_config_entry, source)
    rotated = {**USER_INPUT, CONF_PASSWORD: "rotated"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], rotated)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == step_id
    assert result["errors"] == {"base": expected_error}
    # The re-shown form carries the submitted input, password included.
    schema = result["data_schema"].schema
    assert get_schema_suggested_value(schema, CONF_PASSWORD) == "rotated"
    assert dict(mock_config_entry.data) == USER_INPUT

    mock_client_class.check_connection.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], rotated)

    assert result["type"] is FlowResultType.ABORT
    assert dict(mock_config_entry.data) == rotated


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("reauth", "reauth_successful"),
        ("reconfigure", "reconfigure_successful"),
    ],
    ids=["reauth", "reconfigure"],
)
async def test_mismatched_server_is_confirmed_then_taken_over(
    hass: HomeAssistant,
    mock_client_class: MagicMock,
    mock_config_entry: MockConfigEntry,
    source: str,
    reason: str,
) -> None:
    """A host that answers with other hardware is confirmed before the re-key."""
    mock_config_entry.add_to_hass(hass)
    mock_client_class.check_connection.return_value = OTHER_SERVER_INFO
    moved = {**USER_INPUT, CONF_HOST: "ampio2.test"}

    result = await _start_credentials_flow(hass, mock_config_entry, source)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], moved)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm_server"
    # The description reads these two. A reauth-sourced flow also carries the
    # entry title under "name", which this step's text never references.
    placeholders = result["description_placeholders"]
    assert placeholders is not None
    assert placeholders["host"] == "ampio2.test"
    assert placeholders["mac"] == "0xCEFE"
    # Nothing is written before the user submits the confirmation.
    assert dict(mock_config_entry.data) == USER_INPUT
    assert mock_config_entry.unique_id == MSERV_MAC

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    assert dict(mock_config_entry.data) == moved
    assert mock_config_entry.title == "ampio2.test"
    assert mock_config_entry.unique_id == OTHER_MSERV_MAC


async def test_abandoned_server_confirmation_writes_nothing(
    hass: HomeAssistant,
    mock_client_class: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A closed confirmation dialog leaves the entry as it stands."""
    mock_config_entry.add_to_hass(hass)
    mock_client_class.check_connection.return_value = OTHER_SERVER_INFO

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_HOST: "ampio2.test"}
    )
    assert result["step_id"] == "confirm_server"

    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done()

    assert dict(mock_config_entry.data) == USER_INPUT
    assert mock_config_entry.title == USER_INPUT[CONF_HOST]
    assert mock_config_entry.unique_id == MSERV_MAC


async def test_entry_without_a_key_adopts_the_reported_one(
    hass: HomeAssistant, mock_client_class: MagicMock
) -> None:
    """An entry with no stored identity has nothing to disagree with."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=USER_INPUT[CONF_HOST],
        data=USER_INPUT,
        unique_id=None,
    )
    entry.add_to_hass(hass)
    mock_client_class.check_connection.return_value = OTHER_SERVER_INFO

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == OTHER_MSERV_MAC
