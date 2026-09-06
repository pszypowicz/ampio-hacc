"""Tests for the Ampio binary sensor platform."""

from collections.abc import Generator
from dataclasses import replace
from unittest.mock import MagicMock, patch

from ampio_mqtt import INPUT_KIND_KEYS, InputKind, ObjectRemoved, ObjectUpdated
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.binary_sensor import BINARY_SENSOR_DESCRIPTIONS
from custom_components.ampio.switch import is_switch
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .conftest import emit, make_object, pinned_id

SYSTEM_ENTITY_ID = pinned_id("binary_sensor", 62)
WEJ_ENTITY_ID = pinned_id("binary_sensor", 146)


@pytest.fixture(autouse=True)
def binary_sensor_only() -> Generator[None]:
    """Limit setup to the binary sensor platform so snapshots stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.BINARY_SENSOR]):
        yield


def test_input_kind_vocabulary_is_mapped_or_excluded() -> None:
    """A library upgrade that adds an input kind forces a mapping decision.

    Switchable inputs (the writable flags) belong to the switch platform.
    The system types (``symulacja``, ``detekcja``) are the M-SERV's own
    objects and are deliberately not exposed as entities.
    """
    for key in sorted(INPUT_KIND_KEYS):
        obj = make_object(1, key, 0, leaf_id="0_1_x_0_1")
        assert isinstance(obj.kind, InputKind)
        if obj.is_system:
            assert key not in BINARY_SENSOR_DESCRIPTIONS
        elif obj.kind.switchable:
            assert is_switch(obj)
            assert key not in BINARY_SENSOR_DESCRIPTIONS
        else:
            assert key in BINARY_SENSOR_DESCRIPTIONS


@pytest.mark.usefixtures("mock_client")
async def test_system_typed_objects_are_never_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A system-typed object stays out of the entity set, leaf id or not.

    ``detekcja`` and ``symulacja`` name the M-SERV's own detection and
    presence-simulation objects. The library marks the types as system,
    and the M-SERV serves no state row for them.
    """
    await setup_integration(hass, mock_config_entry)

    assert entity_registry.async_get(SYSTEM_ENTITY_ID) is None


@pytest.mark.usefixtures("mock_client")
async def test_all_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot every entity's registry entry and state."""
    await setup_integration(hass, mock_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


async def test_wej_push_update_toggles_state(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A pushed wired-button input update flips the entity between on and off."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(WEJ_ENTITY_ID).state == STATE_OFF

    obj = replace(mock_client.objects[146], state="1")
    mock_client.objects[146] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(WEJ_ENTITY_ID).state == STATE_ON


async def test_nonzero_values_read_as_on(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The per-object form pushes "255" for on; it must read as on."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[146], state="255")
    mock_client.objects[146] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(WEJ_ENTITY_ID).state == STATE_ON


async def test_removed_object_becomes_unavailable(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Evicting the backing object makes the entity unavailable."""
    await setup_integration(hass, mock_config_entry)

    obj = mock_client.objects.pop(146)
    emit(mock_client, ObjectRemoved(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(WEJ_ENTITY_ID).state == STATE_UNAVAILABLE
