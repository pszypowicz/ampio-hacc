"""Tests for the runtime discovery of the Ampio integration."""

from dataclasses import replace
from datetime import timedelta
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from ampio_mqtt import (
    AccessTier,
    AmpioConnectionError,
    AmpioObject,
    ObjectAdded,
    ObjectRemoved,
    ObjectUpdated,
)
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio import async_remove_config_entry_device
from custom_components.ampio.const import DOMAIN
from custom_components.ampio.data import AmpioData
from homeassistant.const import ATTR_RESTORED, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.util import dt as dt_util

from . import setup_integration
from .conftest import (
    DEFAULT_ROOMS,
    HUB_IDENTIFIER,
    MSENS_IDENTIFIER,
    emit,
    make_object,
    module_pinned_id,
    pinned_id,
    unique_id,
)

NEW_INPUT_ID = 200
NEW_INPUT_ENTITY_ID = pinned_id("binary_sensor", NEW_INPUT_ID)
WEJ_ENTITY_ID = pinned_id("binary_sensor", 146)
RELAY_SWITCH_ID = pinned_id("switch", 74)
RELAY_LIGHT_ID = pinned_id("light", 74)
RELAY_PULSE_ID = pinned_id("sensor", 74, "_pulse")
ISSUE_ID = "stale_records"


def _new_input(**overrides: Any) -> AmpioObject:
    """A wired input added in Designer after setup, on the default module."""
    fields: dict[str, Any] = {
        "leaf_id": "0_cb8f_wej_0_12",
        "funkcja": 12,
        "opis_menu": "Przycisk taras",
        "state": "0",
    }
    fields.update(overrides)
    return make_object(NEW_INPUT_ID, "wej", 7, **fields)


async def _settle(hass: HomeAssistant) -> None:
    """Let the reconcile cooldown elapse and the batch finish.

    The batch runs as an entry background task, which the default
    ``async_block_till_done`` does not wait for.
    """
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done(wait_background_tasks=True)


async def _add(hass: HomeAssistant, client: MagicMock, obj: AmpioObject) -> None:
    client.objects[obj.id] = obj
    emit(client, ObjectAdded(object=obj))
    await _settle(hass)


async def _update(hass: HomeAssistant, client: MagicMock, obj: AmpioObject) -> None:
    client.objects[obj.id] = obj
    emit(client, ObjectUpdated(object=obj))
    await _settle(hass)


async def _remove(hass: HomeAssistant, client: MagicMock, oid: int) -> AmpioObject:
    obj: AmpioObject = client.objects.pop(oid)
    emit(client, ObjectRemoved(object=obj))
    await _settle(hass)
    return obj


def _child(
    device_registry: dr.DeviceRegistry, entry: MockConfigEntry, oid: int
) -> dr.ChildDeviceEntry | None:
    return device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(oid)), entry.entry_id
    )


async def test_new_object_gets_its_entity_device_and_area(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    area_registry: ar.AreaRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """An object added in Designer appears under its module, in its app room."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(NEW_INPUT_ENTITY_ID) is None
    mock_client.fetch_rooms.return_value = {**DEFAULT_ROOMS, NEW_INPUT_ID: "Taras"}

    await _add(hass, mock_client, _new_input())

    assert entity_registry.async_get(NEW_INPUT_ENTITY_ID) == snapshot
    assert hass.states.get(NEW_INPUT_ENTITY_ID) == snapshot
    child = _child(device_registry, mock_config_entry, NEW_INPUT_ID)
    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert child is not None
    assert module is not None
    assert child.parent_device_id == module.id
    assert child.name == "Przycisk taras"
    taras = area_registry.async_get_area_by_name("Taras")
    assert taras is not None
    assert child.area_id == taras.id
    assert mock_client.fetch_rooms.await_count == 2


async def test_new_module_row_gets_a_device_on_a_restricted_account(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A row the tree has not met yet gets its module device before its child."""
    mock_client.modules = {}
    mock_client.mserv = None
    mock_client.access_tier = AccessTier.RESTRICTED
    await setup_integration(hass, mock_config_entry)

    await _add(
        hass, mock_client, _new_input(id_urzadzenia=21, leaf_id="0_d009_wej_0_1")
    )

    hub = device_registry.async_get_device_by_identifier(
        HUB_IDENTIFIER, mock_config_entry.entry_id
    )
    module = device_registry.async_get_device_by_identifier(
        (DOMAIN, "module:21"), mock_config_entry.entry_id
    )
    child = _child(device_registry, mock_config_entry, NEW_INPUT_ID)
    assert hub is not None
    assert module is not None
    assert child is not None
    assert module.name == "Ampio module 0xD009"
    assert module.via_device_id == hub.id
    assert child.parent_device_id == module.id
    assert hass.states.get(NEW_INPUT_ENTITY_ID).state == STATE_OFF


async def test_one_batch_per_burst_of_events(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The many events of one catalogue reply fold into one batch and one room fetch."""
    await setup_integration(hass, mock_config_entry)

    for offset in range(20):
        obj = make_object(
            300 + offset,
            "wej",
            7,
            leaf_id=f"0_cb8f_wej_0_{20 + offset}",
            funkcja=20 + offset,
            state="0",
        )
        mock_client.objects[obj.id] = obj
        emit(mock_client, ObjectAdded(object=obj))
    await _settle(hass)

    assert mock_client.fetch_rooms.await_count == 2
    for offset in range(20):
        state = hass.states.get(pinned_id("binary_sensor", 300 + offset))
        assert state is not None
        assert state.state == STATE_OFF


async def test_state_push_schedules_no_batch(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An update that changes no catalogue field reaches the entity and nothing else."""
    await setup_integration(hass, mock_config_entry)

    await _update(hass, mock_client, replace(mock_client.objects[146], state="1"))

    assert hass.states.get(WEJ_ENTITY_ID).state == STATE_ON
    mock_client.fetch_rooms.assert_awaited_once()


async def test_removed_object_loses_its_entity_and_keeps_its_record(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A row that drops out of the catalogue leaves a restored record, and a re-add restores the id."""
    await setup_integration(hass, mock_config_entry)

    obj = await _remove(hass, mock_client, 146)

    state = hass.states.get(WEJ_ENTITY_ID)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
    assert state.attributes[ATTR_RESTORED] is True
    assert entity_registry.async_get(WEJ_ENTITY_ID) is not None

    await _add(hass, mock_client, obj)

    assert hass.states.get(WEJ_ENTITY_ID).state == STATE_OFF


async def test_hidden_object_loses_its_entity_until_shown_again(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """On the admin tier a delete is the hidden bit, and an un-hide brings the entity back."""
    await setup_integration(hass, mock_config_entry)
    relay = mock_client.objects[74]

    await _update(hass, mock_client, replace(relay, params=16))
    state = hass.states.get(RELAY_SWITCH_ID)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
    assert state.attributes[ATTR_RESTORED] is True

    await _update(hass, mock_client, relay)
    assert hass.states.get(RELAY_SWITCH_ID).state == STATE_ON


async def test_retagged_relay_moves_from_switch_to_light(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A Lighting tag set in Designer swaps the platform without a reload."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(RELAY_LIGHT_ID) is None

    await _update(
        hass, mock_client, replace(mock_client.objects[74], matter_device_type=0x0100)
    )

    assert hass.states.get(RELAY_SWITCH_ID).state == STATE_UNAVAILABLE
    assert hass.states.get(RELAY_LIGHT_ID).state == STATE_ON


async def test_pulse_time_adds_and_removes_the_diagnostic(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The pulse sensor follows the Designer time on the object."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(RELAY_PULSE_ID) is None
    relay = mock_client.objects[74]

    await _update(hass, mock_client, replace(relay, czas=300))
    assert hass.states.get(RELAY_PULSE_ID).state == "3.0"

    await _update(hass, mock_client, relay)
    assert hass.states.get(RELAY_PULSE_ID).state == STATE_UNAVAILABLE


async def test_moved_object_is_removed_and_deletable(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A move in Designer removes the entities, warns once, and permits the delete."""
    await setup_integration(hass, mock_config_entry)
    child = _child(device_registry, mock_config_entry, 74)
    assert child is not None

    await _update(
        hass,
        mock_client,
        replace(mock_client.objects[74], id_urzadzenia=3, leaf_id="0_be82_rel_0_1"),
    )

    assert hass.states.get(RELAY_SWITCH_ID).state == STATE_UNAVAILABLE
    assert hass.states.get(RELAY_LIGHT_ID) is None
    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING
        and "Object 74 moved" in record.getMessage()
    ]
    assert len(warnings) == 1
    stuck = _child(device_registry, mock_config_entry, 74)
    assert stuck is not None
    assert stuck.id == child.id
    assert await async_remove_config_entry_device(hass, mock_config_entry, stuck)


async def test_room_fetch_failure_degrades_the_batch(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed room fetch costs the area seed and nothing else."""
    await setup_integration(hass, mock_config_entry)
    mock_client.fetch_rooms.side_effect = AmpioConnectionError("down")

    await _add(hass, mock_client, _new_input())

    assert hass.states.get(NEW_INPUT_ENTITY_ID).state == STATE_OFF
    child = _child(device_registry, mock_config_entry, NEW_INPUT_ID)
    assert child is not None
    assert child.area_id is None
    warnings = [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and "room map" in record.getMessage()
    ]
    assert len(warnings) == 1


async def test_unload_drops_the_subscription(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """After unload no listener is left for a late catalogue event."""
    await setup_integration(hass, mock_config_entry)

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_client.live_subscriptions == []


async def test_removed_object_is_listed_by_the_repair(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """The repair lists a removed object within one batch, and a re-add clears it."""
    mock_client.access_tier = AccessTier.RESTRICTED
    await setup_integration(hass, mock_config_entry)
    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None

    obj = await _remove(hass, mock_client, 146)

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_key == "stale_records_not_served"
    assert issue.translation_placeholders == {
        "count": "1",
        "names": "- Przycisk kino",
    }

    await _add(hass, mock_client, obj)

    assert issue_registry.async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_hidden_object_is_listed_by_the_repair(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """On the admin tier the wording says deleted, because the row is hidden."""
    await setup_integration(hass, mock_config_entry)

    await _update(hass, mock_client, replace(mock_client.objects[74], params=16))

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_key == "stale_records_deleted"
    assert issue.translation_placeholders == {"count": "1", "names": "- Object 74"}


async def test_moved_object_is_listed_by_the_repair(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A moved object's stuck child is what the repair offers to delete."""
    await setup_integration(hass, mock_config_entry)

    await _update(
        hass,
        mock_client,
        replace(mock_client.objects[74], id_urzadzenia=3, leaf_id="0_be82_rel_0_1"),
    )

    issue = issue_registry.async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    assert issue.translation_placeholders == {"count": "1", "names": "- Object 74"}


async def test_deleting_a_moved_child_brings_it_back_under_the_new_module(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    area_registry: ar.AreaRegistry,
) -> None:
    """The delete the hook permits is the move, and the next batch completes it."""
    await setup_integration(hass, mock_config_entry)
    child = _child(device_registry, mock_config_entry, 74)
    assert child is not None
    piwnica = area_registry.async_get_or_create("Piwnica")
    device_registry.async_update_child_device(
        child.id, name_by_user="Przekaznik piwnica", area_id=piwnica.id
    )

    await _update(
        hass,
        mock_client,
        replace(mock_client.objects[74], id_urzadzenia=3, leaf_id="0_be82_rel_0_1"),
    )
    stuck = _child(device_registry, mock_config_entry, 74)
    assert stuck is not None
    assert await async_remove_config_entry_device(hass, mock_config_entry, stuck)
    device_registry.async_remove_device(stuck.id)
    await _settle(hass)

    moved = _child(device_registry, mock_config_entry, 74)
    new_module = device_registry.async_get_device_by_identifier(
        (DOMAIN, "module:3"), mock_config_entry.entry_id
    )
    assert moved is not None
    assert new_module is not None
    assert moved.id == child.id
    assert moved.parent_device_id == new_module.id
    assert moved.name_by_user == "Przekaznik piwnica"
    assert moved.area_id == piwnica.id
    assert hass.states.get(RELAY_SWITCH_ID).state == STATE_ON


async def test_module_factory_builds_now_and_for_a_new_row(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A module factory runs once per module device: at registration, then per new row.

    The default catalogue puts every module-owned object on row 17, so the
    registration builds for that row alone. A row the tree meets in a later
    batch is built through the platform the factory was registered on. An
    ``admin_only`` factory builds on the same rows, because the fixture
    defaults to the administrator tier.
    """
    await setup_integration(hass, mock_config_entry)
    data: AmpioData = mock_config_entry.runtime_data
    platform = MagicMock(spec=EntityPlatform)
    platform.async_add_entities = AsyncMock()
    built: list[int] = []
    gated_built: list[int] = []

    def factory(_data: AmpioData, module_id: int) -> list[Entity]:
        built.append(module_id)
        return [MagicMock(spec=Entity)]

    def gated_factory(_data: AmpioData, module_id: int) -> list[Entity]:
        gated_built.append(module_id)
        return [MagicMock(spec=Entity)]

    async_add_entities = MagicMock()
    with patch(
        "custom_components.ampio.data.async_get_current_platform",
        return_value=platform,
    ):
        data.async_add_module_platform(factory, async_add_entities)
        data.async_add_module_platform(
            gated_factory, async_add_entities, admin_only=True
        )

    assert built == [17]
    assert gated_built == [17]
    assert async_add_entities.call_count == 2
    for call in async_add_entities.call_args_list:
        assert len(call.args[0]) == 1
    assert data.ensure_module_device(mock_client.objects[36]) is None

    await _add(
        hass, mock_client, _new_input(id_urzadzenia=21, leaf_id="0_d009_wej_0_1")
    )

    assert built == [17, 21]
    assert gated_built == [17, 21]
    assert platform.async_add_entities.await_count == 2
    for call in platform.async_add_entities.call_args_list:
        assert len(call.args[0]) == 1


async def test_gated_module_factory_stays_withheld_on_a_restricted_account(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A restricted account never adds a gated factory's entities, new row or not.

    ``withheld_unique_ids()`` calls a gated factory too, on purpose, to
    compute the ids it withholds, so a raw call count on the factory itself
    would not tell a real build apart from that bookkeeping. What must stay
    empty is the entities a gated registration hands to Home Assistant,
    both at registration and in a later batch.
    """
    mock_client.access_tier = AccessTier.RESTRICTED
    await setup_integration(hass, mock_config_entry)
    data: AmpioData = mock_config_entry.runtime_data
    platform = MagicMock(spec=EntityPlatform)
    platform.async_add_entities = AsyncMock()
    built: list[int] = []

    def factory(_data: AmpioData, module_id: int) -> list[Entity]:
        built.append(module_id)
        return [MagicMock(spec=Entity)]

    def gated_factory(_data: AmpioData, module_id: int) -> list[Entity]:
        return [MagicMock(spec=Entity)]

    async_add_entities = MagicMock()
    gated_async_add_entities = MagicMock()
    with patch(
        "custom_components.ampio.data.async_get_current_platform",
        return_value=platform,
    ):
        data.async_add_module_platform(factory, async_add_entities)
        data.async_add_module_platform(
            gated_factory, gated_async_add_entities, admin_only=True
        )

    assert built == [17]
    gated_async_add_entities.assert_called_once_with([])

    await _add(
        hass, mock_client, _new_input(id_urzadzenia=21, leaf_id="0_d009_wej_0_1")
    )

    assert built == [17, 21]
    assert platform.async_add_entities.await_count == 1
    assert len(platform.async_add_entities.call_args.args[0]) == 1


async def test_deleted_module_device_comes_back_with_its_row(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A module device the user deleted is built again when its row returns.

    The hook permits the delete once no eligible object resolves to the
    module. The next object on the row then gets the device back with its
    registry id, its child under it, and the module's Identify button.
    """
    await setup_integration(hass, mock_config_entry)
    new_input = _new_input(id_urzadzenia=21, leaf_id="0_d009_wej_0_1")
    await _add(hass, mock_client, new_input)
    module = device_registry.async_get_device_by_identifier(
        (DOMAIN, "module:21"), mock_config_entry.entry_id
    )
    assert module is not None
    button_id = module_pinned_id("button", 21, "_identify")
    assert hass.states.get(button_id) is not None

    await _remove(hass, mock_client, NEW_INPUT_ID)
    assert await async_remove_config_entry_device(hass, mock_config_entry, module)
    device_registry.async_remove_device(module.id)
    await hass.async_block_till_done()
    assert hass.states.get(button_id) is None

    await _add(hass, mock_client, new_input)

    rebuilt = device_registry.async_get_device_by_identifier(
        (DOMAIN, "module:21"), mock_config_entry.entry_id
    )
    child = _child(device_registry, mock_config_entry, NEW_INPUT_ID)
    assert rebuilt is not None
    assert rebuilt.id == module.id
    assert child is not None
    assert child.parent_device_id == rebuilt.id
    assert hass.states.get(NEW_INPUT_ENTITY_ID).state == STATE_OFF
    assert hass.states.get(button_id) is not None
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
