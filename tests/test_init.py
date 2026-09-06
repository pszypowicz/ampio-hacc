"""Tests for the Ampio integration setup and teardown."""

from dataclasses import replace
import logging
from unittest.mock import MagicMock

from ampio_mqtt import (
    AccessTier,
    AmpioAuthError,
    AmpioConnectionError,
    AmpioTimeoutError,
    AuthFailed,
    AvailabilityChanged,
    ConnectionDied,
    DesignerRecord,
    RecordSweep,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ampio import async_remove_config_entry_device
from custom_components.ampio.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

from . import setup_integration
from .conftest import (
    EMPTY_SWEEP,
    HUB_IDENTIFIER,
    MSENS_DEVICE_NAME,
    MSENS_IDENTIFIER,
    MSENS_MAC_NAME,
    MSERV_MAC,
    USER_INPUT,
    emit,
    make_object,
    pinned_id,
    unique_id,
)


async def test_setup_and_unload(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The entry loads, and unloading stops the client."""
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_client.disconnect.assert_awaited_once()


async def test_shutdown_stops_client(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Home Assistant stopping closes the connection.

    Entries are not unloaded at shutdown, so the stop event is the only place
    the client is reached, and a connection left open there is torn down by
    task cancellation and reported as an outage.
    """
    await setup_integration(hass, mock_config_entry)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    mock_client.disconnect.assert_awaited_once()


@pytest.mark.parametrize(
    ("start_result", "expected_state"),
    [
        pytest.param(
            AmpioConnectionError("refused"),
            ConfigEntryState.SETUP_RETRY,
            id="connection-error",
        ),
        pytest.param(
            AmpioAuthError("denied"), ConfigEntryState.SETUP_ERROR, id="auth-error"
        ),
        # A discovery cycle that does not complete in time is retryable.
        pytest.param(False, ConfigEntryState.SETUP_RETRY, id="incomplete-discovery"),
    ],
)
async def test_setup_failure_stops_client(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    start_result: Exception | bool,
    expected_state: ConfigEntryState,
) -> None:
    """A failed start maps to the right entry state and stops the client."""
    mock_client.connect.side_effect = [start_result]

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is expected_state
    mock_client.disconnect.assert_awaited_once()


@pytest.mark.usefixtures("mock_client")
async def test_server_swap_rekeys_the_entry(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A different server at the stored host is taken over, with a warning.

    Every identity the integration writes is server-free, so a replaced
    M-SERV changes no id. The entry takes the new server key as its own.
    """
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id="99999")

    await setup_integration(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.unique_id == MSERV_MAC
    swap_warnings = [
        record
        for record in caplog.records
        if record.levelname == "WARNING" and "99999" in record.getMessage()
    ]
    assert len(swap_warnings) == 1


@pytest.mark.usefixtures("mock_client")
async def test_hub_device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """The hub device carries the server identity; module devices link to it."""
    await setup_integration(hass, mock_config_entry)

    hub = device_registry.async_get_device_by_identifier(
        HUB_IDENTIFIER, mock_config_entry.entry_id
    )
    assert hub is not None

    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert module is not None
    assert module.via_device_id == hub.id


async def test_restricted_account_groups_by_module_mac(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Without the module catalogue, grouping still keys on the leaf-derived mac.

    A standard (non-administrator) account is served the object catalogue
    but no module list. The device tree builds from the leaf-embedded mac
    alone, and the module device falls back to a mac-derived name. Only the
    name and the metadata differ from the administrator tier, and neither
    reaches an entity id.
    """
    mock_client.modules = {}
    mock_client.mserv = None
    mock_client.access_tier = AccessTier.RESTRICTED

    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    # The description records answer the admin login only.
    mock_client.resolve_records.assert_not_called()

    hub = device_registry.async_get_device_by_identifier(
        HUB_IDENTIFIER, mock_config_entry.entry_id
    )
    assert hub is not None
    assert hub.name == "M-SERV"

    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert module is not None
    assert module.name == MSENS_MAC_NAME
    assert module.model is None
    assert module.via_device_id == hub.id

    entities = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    assert len(entities) == 26
    # The tree is three deep. Scenes sit on the hub. Every object sits on
    # a child device of its own, under its module, or under the hub for a
    # server-owned object. The tier changes no parent and no identifier.
    hub_unique_ids = {unique_id(121)}
    for entity in entities:
        if entity.domain == "scene":
            assert entity.device_id == hub.id
            continue
        assert entity.device_id is not None
        child = device_registry.async_get(entity.device_id)
        assert isinstance(child, dr.ChildDeviceEntry)
        expected = hub.id if entity.unique_id in hub_unique_ids else module.id
        assert child.parent_device_id == expected

    assert len([entity for entity in entities if entity.domain == "scene"]) == 1


async def test_tier_switch_keeps_entity_ids(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """An entry keeps its devices and its entity ids across tier switches.

    The admin module catalogue names the device and decorates the model, so
    the name follows the tier in both directions. Every entity id holds
    still, because the integration pins it and no name composes it.
    """
    admin_modules = mock_client.modules
    admin_mserv = mock_client.mserv
    mock_client.modules = {}
    mock_client.mserv = None

    await setup_integration(hass, mock_config_entry)
    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert module is not None
    entity_devices = {
        entity.entity_id: entity.device_id
        for entity in er.async_entries_for_config_entry(
            entity_registry, mock_config_entry.entry_id
        )
    }

    mock_client.modules = admin_modules
    mock_client.mserv = admin_mserv
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    enriched = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert enriched is not None
    assert enriched.id == module.id
    assert enriched.name == MSENS_DEVICE_NAME
    assert enriched.model == admin_modules[17].model
    assert {
        entity.entity_id: entity.device_id
        for entity in er.async_entries_for_config_entry(
            entity_registry, mock_config_entry.entry_id
        )
    } == entity_devices

    mock_client.modules = {}
    mock_client.mserv = None
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    downgraded = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert downgraded is not None
    assert downgraded.id == module.id
    assert downgraded.name == MSENS_MAC_NAME
    assert downgraded.model is None
    assert {
        entity.entity_id: entity.device_id
        for entity in er.async_entries_for_config_entry(
            entity_registry, mock_config_entry.entry_id
        )
    } == entity_devices


async def test_user_names_never_reach_an_entity_id(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    area_registry: ar.AreaRegistry,
) -> None:
    """An object discovered after a rename still pins its own id.

    Home Assistant composes an entity id from the area name and the device
    name, at first registration. An object that Designer gains later
    registers against a device the user has since renamed and placed, so
    without the pin its id would carry both. The pin holds it to the object
    identity, which is the unique id.
    """
    await setup_integration(hass, mock_config_entry)
    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert module is not None
    area = area_registry.async_get_or_create("Kuchnia")
    device_registry.async_update_device(
        module.id, area_id=area.id, name_by_user="Sufit kuchnia"
    )

    mock_client.objects[500] = make_object(
        500, "temp", 1, leaf_id="0_cb8f_temp_0_9", opis_menu="Nowy"
    )
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get_entity_id(
        "sensor", DOMAIN, unique_id(500)
    ) == pinned_id("sensor", 500)


async def test_runtime_auth_failure_reloads_into_auth_error(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A credential rejection after startup surfaces as an entry auth error.

    The library's reconnect loop stops for good on an unauthorized reconnect;
    the integration schedules a reload, whose setup then raises
    ConfigEntryAuthFailed and lands the entry in SETUP_ERROR.
    """
    await setup_integration(hass, mock_config_entry)
    mock_client.connect.side_effect = AmpioAuthError("credentials changed")

    emit(mock_client, AuthFailed(reason="not authorized"))
    await hass.async_block_till_done()

    assert "reloading" in caplog.text
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert mock_config_entry.error_reason_translation_key == "invalid_auth"


async def test_connection_died_reloads_and_recovers(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A terminal connection-loop crash re-runs setup and recovers."""
    await setup_integration(hass, mock_config_entry)

    emit(mock_client, ConnectionDied(reason="internal error"))
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_client.connect.await_count == 2


async def test_availability_transitions_log_once_per_edge(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One warning on loss, one info on restore, nothing on first connect."""
    await setup_integration(hass, mock_config_entry)

    with caplog.at_level(logging.INFO, logger="custom_components.ampio"):
        emit(mock_client, AvailabilityChanged(available=True))
        emit(mock_client, AvailabilityChanged(available=False))
        emit(mock_client, AvailabilityChanged(available=True))

    assert caplog.text.count("Connection to the Ampio server lost") == 1
    assert caplog.text.count("Connection to the Ampio server restored") == 1


@pytest.mark.usefixtures("mock_client")
async def test_module_devices_preregistered(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Module devices exist after setup, named and decorated by the catalogue."""
    await setup_integration(hass, mock_config_entry)

    hub = device_registry.async_get_device_by_identifier(
        HUB_IDENTIFIER, mock_config_entry.entry_id
    )
    module_device = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert hub is not None
    assert module_device is not None
    assert hub.area_id is None
    assert module_device.area_id is None
    assert module_device.name == MSENS_DEVICE_NAME
    assert module_device.model == "M-SENS"
    assert module_device.via_device_id == hub.id


@pytest.mark.usefixtures("mock_client")
async def test_duplicate_leaf_builds_both_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two Designer views of one output each get an entity.

    The pair differs in the database id alone. Keying on the leafId
    collapsed them into one entity and made the entity platform report
    the integration for duplicate unique ids.
    """
    await setup_integration(hass, mock_config_entry)

    for object_id in (150, 151):
        assert (
            entity_registry.async_get_entity_id("button", DOMAIN, unique_id(object_id))
            is not None
        )
        assert (
            entity_registry.async_get_entity_id(
                "sensor", DOMAIN, unique_id(object_id, "_pulse")
            )
            is not None
        )

    assert "does not generate unique IDs" not in caplog.text


async def test_room_fetch_failure_degrades(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed room fetch logs one warning and setup still succeeds."""
    mock_client.fetch_rooms.side_effect = AmpioTimeoutError("no reply")
    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    room_map_warnings = [
        record
        for record in caplog.records
        if record.levelname == "WARNING" and "room map" in record.getMessage()
    ]
    assert len(room_map_warnings) == 1


async def test_sweep_never_moves_an_entity(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A record-borne Matter tag decorates; it never changes the partition.

    The description records answer the admin login only, and an entity's
    platform must build identically on both account tiers. Object 74 is a
    relay with an empty catalogue column; a Lighting tag in the record
    bundle the sweep fills must leave the relay on the switch platform.
    """

    def _resolve() -> RecordSweep:
        obj = mock_client.objects[74]
        record = DesignerRecord(matter_device_type=0x0100)
        mock_client.objects[74] = replace(obj, record=record)
        return EMPTY_SWEEP

    mock_client.resolve_records.side_effect = _resolve

    await setup_integration(hass, mock_config_entry)

    mock_client.resolve_records.assert_awaited_once_with()
    assert (
        entity_registry.async_get_entity_id("switch", DOMAIN, unique_id(74)) is not None
    )
    assert entity_registry.async_get_entity_id("light", DOMAIN, unique_id(74)) is None


async def test_resolve_failure_degrades(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed description sweep logs one warning and setup still succeeds."""
    mock_client.resolve_records.side_effect = AmpioTimeoutError("no reply")
    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    warnings = [
        record
        for record in caplog.records
        if record.levelname == "WARNING"
        and "Designer descriptions" in record.getMessage()
    ]
    assert len(warnings) == 1


async def test_admin_records_never_set_an_area(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    area_registry: ar.AreaRegistry,
) -> None:
    """No device takes an area from the admin-only Designer records.

    The sweep answers an administrator alone, and the Designer location is
    not the Home Assistant area map. Where a device belongs is the user's
    call, so the integration seeds nothing.
    """

    def _resolve() -> RecordSweep:
        for oid, location in ((81, "Elsewhere"), (82, "Garaz")):
            mock_client.objects[oid] = replace(
                mock_client.objects[oid], record=DesignerRecord(location=location)
            )
        return EMPTY_SWEEP

    mock_client.resolve_records.side_effect = _resolve

    await setup_integration(hass, mock_config_entry)

    assert area_registry.async_get_area_by_name("Garaz") is None
    devices = dr.async_entries_for_config_entry(
        device_registry, mock_config_entry.entry_id
    )
    assert devices
    assert all(device.area_id is None for device in devices)


@pytest.mark.usefixtures("mock_client")
async def test_every_object_gets_a_child_device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """One child device per object, under its module, named after the object.

    The primary entity carries no name of its own, so its friendly name is
    the device name. An unnamed object reads a translated placeholder, and
    its entity keeps the platform's kind name beside it.
    """
    await setup_integration(hass, mock_config_entry)

    hub = device_registry.async_get_device_by_identifier(
        HUB_IDENTIFIER, mock_config_entry.entry_id
    )
    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert hub is not None
    assert module is not None

    for domain, object_id, name, friendly in (
        ("light", 71, "Taras LED", "Taras LED"),
        ("sensor", 36, "Temperatura", "Temperatura"),
        ("climate", 91, "Termostat Salon", "Termostat Salon"),
        ("switch", 74, "Object 74", "Object 74"),
        ("sensor", 43, "Object 43", "Object 43 CO2"),
    ):
        child = device_registry.async_get_child_device_by_identifier(
            (DOMAIN, unique_id(object_id)), mock_config_entry.entry_id
        )
        assert child is not None
        assert child.parent_device_id == module.id
        assert child.name == name
        entity = entity_registry.async_get(pinned_id(domain, object_id))
        assert entity is not None
        assert entity.device_id == child.id
        state = hass.states.get(pinned_id(domain, object_id))
        assert state is not None
        assert state.attributes["friendly_name"] == friendly

    flag = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(121)), mock_config_entry.entry_id
    )
    assert flag is not None
    assert flag.parent_device_id == hub.id
    assert flag.name == "Dom pusty"

    # A bell's pulse diagnostic rides the bell's child with its own name.
    bell = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(150)), mock_config_entry.entry_id
    )
    assert bell is not None
    pulse = entity_registry.async_get(pinned_id("sensor", 150, "_pulse"))
    assert pulse is not None
    assert pulse.device_id == bell.id
    pulse_state = hass.states.get(pinned_id("sensor", 150, "_pulse"))
    assert pulse_state is not None
    assert pulse_state.attributes["friendly_name"] == "Dzwonek Pulse time"

    for entity in er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    ):
        if entity.domain == "scene":
            assert entity.device_id == hub.id
        else:
            assert entity.device_id not in {hub.id, module.id}


async def test_remove_config_entry_device(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Only devices with no live backing object may be deleted."""
    await setup_integration(hass, mock_config_entry)

    hub = device_registry.async_get_device_by_identifier(
        HUB_IDENTIFIER, mock_config_entry.entry_id
    )
    module_device = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert hub is not None
    assert module_device is not None

    assert not await async_remove_config_entry_device(hass, mock_config_entry, hub)
    assert not await async_remove_config_entry_device(
        hass, mock_config_entry, module_device
    )

    # A per-object device from the earlier topology matches no live module,
    # so the user can delete it. Home Assistant keeps such a device itself:
    # its cleanup pass spares every device that names a live config entry.
    stale = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, f"{MSERV_MAC}:obj:leaf_0_cb8f_led_0_1")},
        name="Taras LED",
        via_device_id=module_device.id,
    )
    assert await async_remove_config_entry_device(hass, mock_config_entry, stale)

    # Drop every object on the module: its device goes stale too.
    for object_id in [
        obj.id for obj in mock_client.objects.values() if obj.module_mac == 52111
    ]:
        del mock_client.objects[object_id]
    assert await async_remove_config_entry_device(
        hass, mock_config_entry, module_device
    )
