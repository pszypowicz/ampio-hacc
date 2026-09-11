"""Tests for the panel buzzer of the Ampio integration."""

from unittest.mock import MagicMock, patch

from ampio_mqtt import AccessTier
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.siren import (
    ATTR_DURATION,
    ATTR_TONE,
    DOMAIN as SIREN_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .conftest import with_buzzer

BUZZER_ENTITY_ID = "siren.ampio_module_17_buzzer"


@pytest.fixture
def siren_only() -> object:
    """Limit setup to the siren platform so the assertions stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.SIREN]):
        yield


async def _turn_on(hass: HomeAssistant, **data: object) -> None:
    await hass.services.async_call(
        SIREN_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: BUZZER_ENTITY_ID, **data},
        blocking=True,
    )


@pytest.mark.usefixtures("siren_only")
async def test_buzzer_only_on_a_module_that_reports_one(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """The capability map decides, so a module without a buzzer gets no siren."""
    await setup_integration(hass, mock_config_entry)
    assert entity_registry.async_get(BUZZER_ENTITY_ID) is None


@pytest.mark.usefixtures("siren_only")
async def test_buzzer_exists_when_the_module_reports_one(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A module whose capabilities carry the buzzer gets a siren."""
    with_buzzer(mock_client)

    await setup_integration(hass, mock_config_entry)

    entry = entity_registry.async_get(BUZZER_ENTITY_ID)
    assert entry is not None
    assert entry.unique_id == "module_17_buzzer"


@pytest.mark.usefixtures("siren_only")
async def test_turn_on_with_a_duration_sends_one_cycle(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A timed call is one cycle of the sequence frame, not the simple one.

    The simple frame caps at 2.55 s, and a siren duration may exceed it.
    """
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)

    await _turn_on(hass, **{ATTR_TONE: 4, ATTR_DURATION: 5})

    mock_client.buzz_pattern.assert_awaited_once_with(17, tone=4, seconds=5.0, cycles=1)
    assert hass.states.get(BUZZER_ENTITY_ID).state == "on"


@pytest.mark.usefixtures("siren_only")
async def test_turn_on_without_a_duration_latches(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """No duration means sound until stopped, which is cycles zero."""
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)

    await _turn_on(hass)

    call = mock_client.buzz_pattern.await_args
    assert call.kwargs["cycles"] == 0
    assert call.kwargs["tone"] == 6


@pytest.mark.usefixtures("siren_only")
async def test_turn_off_stops_the_buzzer(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Stopping sends the library's stop pair and clears the state."""
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)
    await _turn_on(hass)

    await hass.services.async_call(
        SIREN_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: BUZZER_ENTITY_ID},
        blocking=True,
    )

    mock_client.buzz_stop.assert_awaited_once_with(17)
    assert hass.states.get(BUZZER_ENTITY_ID).state == "off"


@pytest.mark.usefixtures("siren_only")
async def test_withheld_enumeration_names_every_row(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A standard account cannot read capabilities, so it names every row.

    The enumeration is what tells a withheld record apart from one Ampio
    Designer dropped. A bare capability check would name none of them, and
    an orphaned buzzer record would land in the wrong repair card.
    """
    mock_client.access_tier = AccessTier.RESTRICTED

    await setup_integration(hass, mock_config_entry)

    withheld = mock_config_entry.runtime_data.withheld_unique_ids()
    assert "module_17_buzzer" in withheld
