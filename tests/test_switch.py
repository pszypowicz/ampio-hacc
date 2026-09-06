"""Tests for the Ampio switch platform."""

from collections.abc import Generator
from dataclasses import replace
from unittest.mock import MagicMock, patch

from ampio_mqtt import ObjectUpdated
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from . import setup_integration
from .conftest import HUB_IDENTIFIER, MSENS_IDENTIFIER, emit, make_object, pinned_id

PLAIN_ENTITY_ID = pinned_id("switch", 74)
OUTLET_ENTITY_ID = pinned_id("switch", 75)
FLAG_ENTITY_ID = pinned_id("switch", 61)


@pytest.fixture(autouse=True)
def switch_only() -> Generator[None]:
    """Limit setup to the switch platform so snapshots stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.SWITCH]):
        yield


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


async def test_turn_on_off_maps_to_verbs(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The switch services map to the plain on and off verbs."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: OUTLET_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_on.assert_awaited_once_with(75)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: OUTLET_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(75)


async def test_light_tagged_relay_is_not_a_switch(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A relay whose Matter tag says light belongs to the light platform."""
    await setup_integration(hass, mock_config_entry)

    # Two relays plus the two writable flags.
    states = [s for s in hass.states.async_all() if s.domain == "switch"]
    assert len(states) == 4
    assert not any("kinkiet" in s.entity_id for s in states)


async def test_flag_services_map_to_verbs(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A writable flag answers the switch services with the plain verbs."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(FLAG_ENTITY_ID).state == STATE_ON

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: FLAG_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(61)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: FLAG_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_on.assert_awaited_once_with(61)


async def test_flag_push_update_toggles_state(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A pushed flag update flips the switch between on and off."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(FLAG_ENTITY_ID).state == STATE_ON

    obj = replace(mock_client.objects[61], state="0")
    mock_client.objects[61] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(FLAG_ENTITY_ID).state == STATE_OFF


async def test_push_echo_toggles_state(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A pushed relay echo flips the entity between on and off."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(PLAIN_ENTITY_ID).state == STATE_ON

    obj = replace(mock_client.objects[74], state="0")
    mock_client.objects[74] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(PLAIN_ENTITY_ID).state == STATE_OFF


async def test_timed_relay_pulses_on_turn_on(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A configured Designer time turns the on write into a timed pulse.

    The M-SERV never applies the time server-side, so the integration
    sends it, as the Ampio app does. The off write stays plain.
    """
    obj = mock_client.objects[74]
    mock_client.objects[74] = replace(obj, pulse_ms=90000)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: PLAIN_ENTITY_ID},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_once_with(74, 255, pulse_ms=90000)
    mock_client.turn_on.assert_not_called()

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: PLAIN_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(74)


async def test_read_only_object_rejects_writes(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A Designer read-only object raises instead of sending a doomed write.

    The M-SERV drops such writes silently on both account tiers, so the
    entity rejects them up front and keeps its platform.
    """
    obj = mock_client.objects[61]
    # Designer's read-only checkbox is params bit 6; ``read_only`` derives.
    mock_client.objects[61] = replace(obj, params=obj.params | (1 << 6))
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: FLAG_ENTITY_ID},
            blocking=True,
        )
    mock_client.turn_off.assert_not_called()


async def test_leafless_object_parents_through_its_siblings(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A relay whose leaf id Designer cleared still joins its module.

    Unchecking an object's Matter box saves the row without ``leafId``.
    The library reads the module mac out of the leaf-bearing siblings on
    the same module id, on both account tiers, and the child parents
    through it.
    """
    mock_client.objects[98] = make_object(
        98,
        "przekaznik",
        0,
        leaf_id="",
        opis_menu="Leafless",
        state="0",
        sibling_module_mac=52111,
    )
    await setup_integration(hass, mock_config_entry)

    entry = entity_registry.async_get(pinned_id("switch", 98))
    assert entry is not None
    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert module is not None
    assert entry.device_id is not None
    child = device_registry.async_get(entry.device_id)
    assert isinstance(child, dr.ChildDeviceEntry)
    assert child.parent_device_id == module.id
    assert hass.states.get(pinned_id("switch", 98)).state == STATE_OFF


async def test_leafless_object_without_siblings_parents_to_the_hub(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Without a leaf mac and without a sibling mac, the hub takes the child."""
    mock_client.objects[98] = make_object(
        98,
        "przekaznik",
        0,
        leaf_id="",
        id_urzadzenia=999,
        opis_menu="Leafless",
        state="0",
    )
    await setup_integration(hass, mock_config_entry)

    entry = entity_registry.async_get(pinned_id("switch", 98))
    assert entry is not None
    hub = device_registry.async_get_device_by_identifier(
        HUB_IDENTIFIER, mock_config_entry.entry_id
    )
    assert hub is not None
    assert entry.device_id is not None
    child = device_registry.async_get(entry.device_id)
    assert isinstance(child, dr.ChildDeviceEntry)
    assert child.parent_device_id == hub.id
