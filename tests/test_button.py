"""Tests for the Ampio button platform."""

from collections.abc import Generator
from dataclasses import replace
from datetime import timedelta
import logging
from unittest.mock import MagicMock, patch

from ampio_mqtt import (
    AccessTier,
    AmpioConnectionError,
    AmpioTimeoutError,
    AvailabilityChanged,
    ObjectAdded,
)
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.button import IDENTIFY_HOLD_SECONDS
from custom_components.ampio.const import DOMAIN
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from . import setup_integration
from .conftest import (
    MSENS_IDENTIFIER,
    emit,
    make_object,
    module_pinned_id,
    pinned_id,
    set_access_tier,
    unique_id,
)

RELAY_ENTITY_ID = pinned_id("button", 150)
FLAG_ENTITY_ID = pinned_id("button", 149)
IDENTIFY_ENTITY_ID = module_pinned_id("button", 17, "_identify")


@pytest.fixture
def button_only() -> Generator[None]:
    """Limit setup to the button platform so snapshots stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.BUTTON]):
        yield


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


async def _elapse(hass: HomeAssistant, seconds: int) -> None:
    """Let ``seconds`` pass for the stop timer, and let its task finish."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


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


@pytest.mark.usefixtures("button_only")
async def test_identify_press_lights_then_stops(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A press sends the identify start, and the stop follows after the hold."""
    await setup_integration(hass, mock_config_entry)

    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert module is not None
    entity = entity_registry.async_get(IDENTIFY_ENTITY_ID)
    assert entity is not None
    assert entity.device_id == module.id
    assert mock_config_entry.runtime_data.withheld_unique_ids() == set()

    await _press(hass, IDENTIFY_ENTITY_ID)

    mock_client.identify.assert_awaited_once_with(17)
    mock_client.identify_stop.assert_not_called()

    await _elapse(hass, IDENTIFY_HOLD_SECONDS + 1)

    mock_client.identify_stop.assert_awaited_once_with(17)


@pytest.mark.usefixtures("button_only")
async def test_identify_second_press_sends_one_stop(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A press during the hold cancels the first timer, so one stop follows.

    The test loop's clock does not advance with the fired time, so the two
    presses schedule the same deadline. What the test proves is that the
    first timer is cancelled, not that the deadline moves.
    """
    await setup_integration(hass, mock_config_entry)

    await _press(hass, IDENTIFY_ENTITY_ID)
    await _elapse(hass, 20)
    await _press(hass, IDENTIFY_ENTITY_ID)

    assert mock_client.identify.await_count == 2
    mock_client.identify_stop.assert_not_called()

    await _elapse(hass, IDENTIFY_HOLD_SECONDS + 1)

    mock_client.identify_stop.assert_awaited_once_with(17)


@pytest.mark.usefixtures("button_only")
async def test_identify_is_withheld_on_a_standard_account(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A standard account gets no identify entity, and keeps its module device.

    The frame rides the CAN write tree, which the M-SERV serves the
    administrator login alone. An entity that could never send it is not
    built at all.
    """
    set_access_tier(mock_client, AccessTier.RESTRICTED)

    await setup_integration(hass, mock_config_entry)

    assert entity_registry.async_get(IDENTIFY_ENTITY_ID) is None
    assert hass.states.get(IDENTIFY_ENTITY_ID) is None
    # The device is built from the Designer row, which both tiers receive.
    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert module is not None
    assert mock_config_entry.runtime_data.withheld_unique_ids() == {
        "module_17_identify"
    }


@pytest.mark.usefixtures("button_only")
async def test_identify_unknown_module_raises(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A module the catalogue cannot address raises, and schedules no stop."""
    mock_client.identify.side_effect = ValueError("module id 17 has no mac")
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as excinfo:
        await _press(hass, IDENTIFY_ENTITY_ID)
    assert excinfo.value.translation_key == "module_not_addressable"
    assert not isinstance(excinfo.value, ServiceValidationError)

    await _elapse(hass, IDENTIFY_HOLD_SECONDS + 1)
    mock_client.identify_stop.assert_not_called()


@pytest.mark.usefixtures("button_only")
@pytest.mark.parametrize(
    "error", [AmpioConnectionError("Not connected"), AmpioTimeoutError("no ack")]
)
async def test_identify_stop_failure_logs(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """A stop the broker does not carry logs once, because the LED stays lit."""
    mock_client.identify_stop.side_effect = error
    await setup_integration(hass, mock_config_entry)

    await _press(hass, IDENTIFY_ENTITY_ID)
    await _elapse(hass, IDENTIFY_HOLD_SECONDS + 1)

    warnings = [
        record
        for record in caplog.records
        if record.name == "custom_components.ampio.button"
        and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "Could not send the identify stop to Ampio module 17" in message


@pytest.mark.usefixtures("button_only")
async def test_identify_unload_sends_stop(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An unload during the hold sends the stop at once, and the timer dies with it."""
    await setup_integration(hass, mock_config_entry)
    await _press(hass, IDENTIFY_ENTITY_ID)

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_client.identify_stop.assert_awaited_once_with(17)

    await _elapse(hass, IDENTIFY_HOLD_SECONDS + 1)
    mock_client.identify_stop.assert_awaited_once_with(17)


@pytest.mark.usefixtures("button_only")
async def test_identify_button_follows_new_module(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A module row met after setup gets its button; the M-SERV row never does."""
    await setup_integration(hass, mock_config_entry)
    hub_button = module_pinned_id("button", 1, "_identify")
    new_button = module_pinned_id("button", 21, "_identify")
    assert entity_registry.async_get(hub_button) is None
    assert entity_registry.async_get(new_button) is None

    obj = make_object(
        200,
        "wej",
        7,
        leaf_id="0_d009_wej_0_1",
        id_urzadzenia=21,
        funkcja=12,
        opis_menu="Przycisk taras",
        state="0",
    )
    mock_client.objects[200] = obj
    emit(mock_client, ObjectAdded(object=obj))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entity_registry.async_get(new_button) is not None
    assert hass.states.get(new_button) is not None
    assert entity_registry.async_get(hub_button) is None


@pytest.mark.usefixtures("button_only")
async def test_identify_follows_the_connection(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The button reads unavailable while the broker connection is down."""
    await setup_integration(hass, mock_config_entry)

    mock_client.available = False
    emit(mock_client, AvailabilityChanged(available=False))
    await hass.async_block_till_done()

    assert hass.states.get(IDENTIFY_ENTITY_ID).state == STATE_UNAVAILABLE
