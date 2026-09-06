"""Tests for the Ampio light platform."""

from collections.abc import Generator
from dataclasses import replace
from unittest.mock import MagicMock, patch

from ampio_mqtt import OUTPUT_KIND_KEYS, ObjectUpdated
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.light import LIGHT_MATTER_TYPES
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGBW_COLOR,
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .conftest import emit, pinned_id

DIMMER_ENTITY_ID = pinned_id("light", 71)
RGBW_ENTITY_ID = pinned_id("light", 72)
RELAY_ENTITY_ID = pinned_id("light", 73)


@pytest.fixture(autouse=True)
def light_only() -> Generator[None]:
    """Limit setup to the light platform so snapshots stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.LIGHT]):
        yield


def test_output_kind_vocabulary_is_split_or_deferred() -> None:
    """A library upgrade that adds an output kind forces a platform decision.

    The light platform takes ``dimmer`` and ``rgbw`` outright, plus ``relay``
    when its Matter tag is in ``LIGHT_MATTER_TYPES``. The cover kinds wait
    for the cover platform, and untagged relays wait for the switch platform.
    """
    assert {
        "relay",
        "dimmer",
        "rgbw",
        "cover",
        "cover_position",
        "cover_tilt",
    } == OUTPUT_KIND_KEYS
    assert {0x0100, 0x0101, 0x010C, 0x010D} == LIGHT_MATTER_TYPES


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


async def test_relay_light_turn_on_off(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An on/off light maps the services to the plain switch verbs."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RELAY_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_on.assert_awaited_once_with(73)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: RELAY_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(73)


async def test_dimmer_brightness_maps_to_set_value(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Turning a dimmer on with a brightness sets the 0-255 level."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: DIMMER_ENTITY_ID, ATTR_BRIGHTNESS: 200},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_once_with(71, 200, pulse_ms=None)


async def test_timed_lights_pulse_on_turn_on(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A configured Designer time turns the on writes into timed pulses.

    The M-SERV never applies the time server-side, so the integration
    sends it, as the Ampio app does - the staircase-timer case.
    """
    for oid, pulse in ((73, 120000), (71, 30000)):
        mock_client.objects[oid] = replace(mock_client.objects[oid], czas=pulse // 10)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RELAY_ENTITY_ID},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_once_with(73, 255, pulse_ms=120000)
    mock_client.turn_on.assert_not_called()

    mock_client.set_value.reset_mock()
    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: DIMMER_ENTITY_ID, ATTR_BRIGHTNESS: 180},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_once_with(71, 180, pulse_ms=30000)


async def test_dimmer_turn_on_without_brightness(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Turning a dimmer on without a brightness uses the plain verb."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: DIMMER_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_on.assert_awaited_once_with(71)


async def test_rgbw_color_and_brightness_scale(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A color with a brightness scales so the peak channel hits it."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {
            ATTR_ENTITY_ID: RGBW_ENTITY_ID,
            ATTR_RGBW_COLOR: (10, 20, 40, 80),
            ATTR_BRIGHTNESS: 160,
        },
        blocking=True,
    )
    mock_client.set_colors.assert_awaited_once_with(72, 20, 40, 80, 160)


async def test_rgbw_turn_on_from_dark_defaults_to_white(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Turning an all-zero rgbw on without arguments raises the white channel."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[72], state="0")
    mock_client.objects[72] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RGBW_ENTITY_ID},
        blocking=True,
    )
    mock_client.set_colors.assert_awaited_once_with(72, 0, 0, 0, 255)


async def test_rgbw_explicit_zero_color_turns_off(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An explicit all-zero color is a request for darkness and routes to off."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[72], state="0")
    mock_client.objects[72] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_ON,
        {
            ATTR_ENTITY_ID: RGBW_ENTITY_ID,
            ATTR_RGBW_COLOR: (0, 0, 0, 0),
            ATTR_BRIGHTNESS: 100,
        },
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(72)
    mock_client.set_colors.assert_not_awaited()


async def test_rgbw_turn_off_uses_turn_off(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The entity delegates rgbw off to the client, which owns the routing."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        LIGHT_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: RGBW_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_off.assert_awaited_once_with(72)


async def test_push_echo_updates_brightness(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A pushed dimmer echo updates state and brightness."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[71], state="255")
    mock_client.objects[71] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    state = hass.states.get(DIMMER_ENTITY_ID)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == 255
