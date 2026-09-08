"""Tests for the stale-record repair of the Ampio integration."""

from dataclasses import replace
from http import HTTPStatus
from unittest.mock import MagicMock

from ampio_mqtt import AccessTier
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.ampio.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.setup import async_setup_component

from . import setup_integration
from .conftest import DEFAULT_SCENES, MSENS_IDENTIFIER, pinned_id, unique_id

ISSUE_ID = "stale_records"
SCENE_ENTITY_ID = "scene.m_serv_wieczor"


async def _reload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


def _leave_records_behind(mock_client: MagicMock) -> None:
    """Soft-delete the relay, evict the dimmer, and retire the active scene."""
    mock_client.objects[74] = replace(mock_client.objects[74], params=16)
    del mock_client.objects[71]
    mock_client.fetch_scenes.return_value = [DEFAULT_SCENES[1]]


async def test_no_issue_without_stale_records(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A setup that claims every record raises nothing."""
    await setup_integration(hass, mock_config_entry)

    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_stale_records_raise_a_fixable_issue(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Records no platform claimed on this setup are listed, one issue for all."""
    await setup_integration(hass, mock_config_entry)
    _leave_records_behind(mock_client)
    await _reload(hass, mock_config_entry)

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.is_fixable
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == "stale_records_deleted"
    assert issue.translation_placeholders == {
        "count": "3",
        "names": "- Object 74\n- Taras LED\n- Wieczór",
    }


async def test_restricted_account_gets_the_not_served_wording(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A restricted account cannot tell a delete from a grant change."""
    await setup_integration(hass, mock_config_entry)
    _leave_records_behind(mock_client)
    mock_client.access_tier = AccessTier.RESTRICTED
    await _reload(hass, mock_config_entry)

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_key == "stale_records_not_served"


async def test_issue_clears_once_the_records_are_claimed_again(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """An object that comes back takes its record with it, and the issue goes."""
    await setup_integration(hass, mock_config_entry)
    relay = mock_client.objects[74]
    mock_client.objects[74] = replace(relay, params=16)
    await _reload(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is not None

    mock_client.objects[74] = relay
    await _reload(hass, mock_config_entry)

    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_disabled_entity_is_not_stale(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """An entity the user disabled is skipped by the platform, not stale."""
    await setup_integration(hass, mock_config_entry)
    entity_registry.async_update_entity(
        pinned_id("switch", 74), disabled_by=er.RegistryEntryDisabler.USER
    )
    await _reload(hass, mock_config_entry)

    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_removing_the_entry_clears_the_issue(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """The records go with the entry, so the issue has nothing left to fix."""
    await setup_integration(hass, mock_config_entry)
    _leave_records_behind(mock_client)
    await _reload(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is not None

    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_fix_flow_removes_the_stale_records(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Confirming the fix deletes the listed records and reloads the entry."""
    assert await async_setup_component(hass, "repairs", {})
    await setup_integration(hass, mock_config_entry)
    _leave_records_behind(mock_client)
    await _reload(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is not None

    client = await hass_client()
    resp = await client.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": ISSUE_ID}
    )
    assert resp.status == HTTPStatus.OK
    flow = await resp.json()
    assert flow["type"] == "form"
    assert flow["step_id"] == "confirm"

    resp = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
    assert resp.status == HTTPStatus.OK
    assert (await resp.json())["type"] == "create_entry"
    await hass.async_block_till_done()

    entry_id = mock_config_entry.entry_id
    for oid in (71, 74):
        assert (
            device_registry.async_get_child_device_by_identifier(
                (DOMAIN, unique_id(oid)), entry_id
            )
            is None
        )
    assert entity_registry.async_get(pinned_id("switch", 74)) is None
    assert entity_registry.async_get(pinned_id("light", 71)) is None
    assert entity_registry.async_get(SCENE_ENTITY_ID) is None
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None
    assert mock_config_entry.state is ConfigEntryState.LOADED

    # Every claimed record is untouched.
    assert entity_registry.async_get(pinned_id("sensor", 36)) is not None
    assert (
        device_registry.async_get_child_device_by_identifier(
            (DOMAIN, unique_id(36)), entry_id
        )
        is not None
    )
    assert (
        device_registry.async_get_device_by_identifier(MSENS_IDENTIFIER, entry_id)
        is not None
    )


async def test_fix_flow_removes_a_module_without_objects(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A module whose every object is gone is stale with its children."""
    assert await async_setup_component(hass, "repairs", {})
    await setup_integration(hass, mock_config_entry)
    for oid in [
        obj.id for obj in mock_client.objects.values() if obj.id_urzadzenia == 17
    ]:
        del mock_client.objects[oid]
    await _reload(hass, mock_config_entry)

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    names = issue.translation_placeholders["names"]
    assert "- m-sens salon\n" in names
    assert "Identify" not in names

    client = await hass_client()
    flow = await (
        await client.post(
            "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": ISSUE_ID}
        )
    ).json()
    resp = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
    assert (await resp.json())["type"] == "create_entry"
    await hass.async_block_till_done()

    entry_id = mock_config_entry.entry_id
    assert (
        device_registry.async_get_device_by_identifier(MSENS_IDENTIFIER, entry_id)
        is None
    )
    assert (
        device_registry.async_get_child_device_by_identifier(
            (DOMAIN, unique_id(36)), entry_id
        )
        is None
    )
    # The server-owned flag and its hub are not on the module and stay.
    assert (
        device_registry.async_get_child_device_by_identifier(
            (DOMAIN, unique_id(121)), entry_id
        )
        is not None
    )
