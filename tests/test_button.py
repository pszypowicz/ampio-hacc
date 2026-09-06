"""Tests for the Ampio button platform."""

from collections.abc import Generator
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.const import DOMAIN
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .conftest import pinned_id, unique_id

RELAY_ENTITY_ID = pinned_id("button", 150)
FLAG_ENTITY_ID = pinned_id("button", 149)


@pytest.fixture
def button_only() -> Generator[None]:
    """Limit setup to the button platform so snapshots stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.BUTTON]):
        yield


@pytest.mark.usefixtures("mock_client", "button_only")
async def test_all_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot every entity's registry entry and state."""
    await setup_integration(hass, mock_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.usefixtures("button_only")
async def test_press_maps_to_turn_on(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A press pulses for the configured time, or latches without one."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: RELAY_ENTITY_ID},
        blocking=True,
    )
    mock_client.set_value.assert_awaited_once_with(150, 255, pulse_ms=3000)
    mock_client.turn_on.assert_not_called()

    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: FLAG_ENTITY_ID},
        blocking=True,
    )
    mock_client.turn_on.assert_awaited_once_with(149)


async def test_bell_wins_over_light_tag(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A bell-marked relay is a button even when its Matter tag says light."""
    obj = mock_client.objects[73]
    mock_client.objects[73] = replace(obj, params=obj.params | (1 << 15))

    with patch("custom_components.ampio.PLATFORMS", [Platform.BUTTON, Platform.LIGHT]):
        await setup_integration(hass, mock_config_entry)

    oid = unique_id(73)
    assert entity_registry.async_get_entity_id("button", DOMAIN, oid) is not None
    assert entity_registry.async_get_entity_id("light", DOMAIN, oid) is None


async def test_bell_flag_is_not_a_switch(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A bell-marked flag leaves the switch platform for the button one."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.BUTTON, Platform.SWITCH]):
        await setup_integration(hass, mock_config_entry)

    oid = unique_id(149)
    assert entity_registry.async_get_entity_id("button", DOMAIN, oid) is not None
    assert entity_registry.async_get_entity_id("switch", DOMAIN, oid) is None


@pytest.mark.usefixtures("button_only")
async def test_read_only_bell_rejects_press(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A Designer read-only bell raises instead of sending a doomed write."""
    obj = mock_client.objects[150]
    mock_client.objects[150] = replace(obj, params=obj.params | (1 << 6))
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            BUTTON_DOMAIN,
            SERVICE_PRESS,
            {ATTR_ENTITY_ID: RELAY_ENTITY_ID},
            blocking=True,
        )
    mock_client.turn_on.assert_not_called()
