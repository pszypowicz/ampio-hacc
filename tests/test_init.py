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
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

from . import setup_integration
from .conftest import (
    DEFAULT_ROOMS,
    EMPTY_SWEEP,
    HUB_IDENTIFIER,
    MSENS_DEVICE_NAME,
    MSENS_IDENTIFIER,
    MSENS_ROW_NAME,
    MSERV_MAC,
    USER_INPUT,
    emit,
    make_object,
    pinned_id,
    set_access_tier,
    unique_id,
)


def _registry_ids(
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    entry: MockConfigEntry,
) -> tuple[set[str], set[str]]:
    """Every device id and entity id the entry holds right now.

    Full devices and child devices come from two different registry
    readers, so a set built from one alone would miss half the tree.
    """
    devices = {
        device.id
        for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    }
    devices |= {
        child.id
        for child in dr.async_child_entries_for_config_entry(
            device_registry, entry.entry_id
        )
    }
    entities = {
        entity.entity_id
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    }
    return devices, entities


def _full_map(
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    entry: MockConfigEntry,
) -> tuple[dict[str, str | None], dict[str, tuple[str, str | None]]]:
    """Every device's parent link and every entity's unique id and device, right now.

    A full device (the hub, a module) reports its parent through
    ``via_device_id``. A child device (an object) reports it through
    ``parent_device_id``. Both come from separate registry readers, so a map
    built from one alone would miss half the tree.
    """
    devices: dict[str, str | None] = {
        device.id: device.via_device_id
        for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    }
    devices |= {
        child.id: child.parent_device_id
        for child in dr.async_child_entries_for_config_entry(
            device_registry, entry.entry_id
        )
    }
    entities = {
        entity.entity_id: (entity.unique_id, entity.device_id)
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    }
    return devices, entities


async def test_reconfigure_keeps_devices_and_entity_ids(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_client_class: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A credential change through the flow moves no device and no entity id.

    This is what the flow exists for. A delete and a fresh add drops every
    record instead. The first three assertions prove that setup really
    re-ran with the new credentials, so that the fourth one means
    something.
    """
    await setup_integration(hass, mock_config_entry)
    before = _registry_ids(device_registry, entity_registry, mock_config_entry)
    moved = {**USER_INPUT, CONF_USERNAME: "admin", CONF_PASSWORD: "rotated"}

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], moved)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert dict(mock_config_entry.data) == moved
    assert mock_client_class.call_args.args == (
        moved[CONF_HOST],
        moved[CONF_USERNAME],
        moved[CONF_PASSWORD],
    )
    assert mock_client.connect.await_count == 2
    assert _registry_ids(device_registry, entity_registry, mock_config_entry) == before


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
    """The hub device carries the server's serial and version; module devices link to it."""
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


async def test_hub_model_falls_back_when_the_catalogue_has_none(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """An administrator whose M-SERV row resolves to no model still gets the product name."""
    mock_client.mserv = replace(mock_client.mserv, typ_urzadzenia=99999)
    assert mock_client.mserv.model is None

    await setup_integration(hass, mock_config_entry)

    hub = device_registry.async_get_device_by_identifier(
        HUB_IDENTIFIER, mock_config_entry.entry_id
    )
    assert hub is not None
    assert hub.model == "M-SERV"


async def test_restricted_account_groups_by_module_row(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Without the module catalogue, grouping still keys on the module row.

    A standard (non-administrator) account is served the object catalogue
    but no module list. The device tree builds from the Designer module row
    id, and the module device falls back to that row id as its name. Only
    the name and the metadata differ from the administrator tier, and
    neither reaches an entity id.
    """
    set_access_tier(mock_client, AccessTier.RESTRICTED)

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
    assert module.name == MSENS_ROW_NAME
    assert module.model is None
    assert module.via_device_id == hub.id

    entities = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    assert len(entities) == 29
    # The tree is three deep. A scene sits directly on the hub. Every object
    # sits on a child device of its own, under its module, or under the hub
    # for a server-owned object. The tier changes no parent and no
    # identifier, and a standard account is given no identify button.
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


async def test_empty_catalogue_moves_no_device_or_entity_id(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """An empty module catalogue moves no device and no entity id.

    The account tier stays ADMIN through every phase here; only the
    catalogue empties and refills, the way a row can drop out of the
    catalogue mid-session while a visible object still points at it. The
    catalogue names the device and decorates the model, so the name follows
    the catalogue in both directions, but the device and every entity id
    hold still regardless, because the integration pins the id and no name
    composes it.
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
    assert downgraded.name == MSENS_ROW_NAME
    assert downgraded.model is None
    assert {
        entity.entity_id: entity.device_id
        for entity in er.async_entries_for_config_entry(
            entity_registry, mock_config_entry.entry_id
        )
    } == entity_devices


async def test_tier_switch_keeps_the_full_device_and_entity_map(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A downgrade and the upgrade back hold every entity id, unique id, and device parent.

    ``set_access_tier`` denies the module catalogue the way the library
    does, so this puts the tier gate itself under test, not a catalogue that
    merely happens to be empty. No name or model an account tier can see may
    move an entity id, a unique id, or a device's parent link.
    """
    await setup_integration(hass, mock_config_entry)
    baseline = _full_map(device_registry, entity_registry, mock_config_entry)

    set_access_tier(mock_client, AccessTier.RESTRICTED)
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert _full_map(device_registry, entity_registry, mock_config_entry) == baseline

    set_access_tier(mock_client, AccessTier.ADMIN)
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert _full_map(device_registry, entity_registry, mock_config_entry) == baseline


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
    ConfigEntryAuthFailed and lands the entry in SETUP_ERROR. The flow handler
    now carries async_step_reauth, so core offers the user a way back instead
    of leaving the entry stuck.
    """
    await setup_integration(hass, mock_config_entry)
    mock_client.connect.side_effect = AmpioAuthError("credentials changed")

    emit(mock_client, AuthFailed(reason="not authorized"))
    await hass.async_block_till_done()

    assert "reloading" in caplog.text
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert mock_config_entry.error_reason_translation_key == "invalid_auth"
    # The flow handler now carries async_step_reauth, so core offers the
    # user a way back instead of leaving the entry stuck.
    assert list(mock_config_entry.async_get_active_flows(hass, {SOURCE_REAUTH}))


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


@pytest.mark.usefixtures("mock_client")
async def test_rooms_seed_child_areas(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    area_registry: ar.AreaRegistry,
) -> None:
    """A child takes its object's app room as its area; no room, no area.

    The room tables ride the data surface both account tiers receive, and
    the seed applies at first creation only. Hub and modules get none: a
    module spans rooms.
    """
    await setup_integration(hass, mock_config_entry)

    for object_id, room in DEFAULT_ROOMS.items():
        child = device_registry.async_get_child_device_by_identifier(
            (DOMAIN, unique_id(object_id)), mock_config_entry.entry_id
        )
        assert child is not None
        area = area_registry.async_get_area_by_name(room)
        assert area is not None
        assert child.area_id == area.id

    roomless = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(74)), mock_config_entry.entry_id
    )
    assert roomless is not None
    assert roomless.area_id is None
    hub = device_registry.async_get_device_by_identifier(
        HUB_IDENTIFIER, mock_config_entry.entry_id
    )
    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert hub is not None and hub.area_id is None
    assert module is not None and module.area_id is None


async def test_area_seed_never_moves_a_device(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    area_registry: ar.AreaRegistry,
) -> None:
    """The room seeds an area once; a later room change moves nothing."""
    await setup_integration(hass, mock_config_entry)
    child = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(36)), mock_config_entry.entry_id
    )
    assert child is not None
    elsewhere = area_registry.async_get_or_create("Garaż")
    device_registry.async_update_child_device(child.id, area_id=elsewhere.id)

    mock_client.fetch_rooms.return_value = {**DEFAULT_ROOMS, 36: "Kuchnia"}
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    moved = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(36)), mock_config_entry.entry_id
    )
    assert moved is not None
    assert moved.id == child.id
    assert moved.area_id == elsewhere.id


async def test_room_fetch_failure_degrades(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
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
    child = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(36)), mock_config_entry.entry_id
    )
    assert child is not None
    assert child.area_id is None


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


async def test_failed_sweep_stops_setup(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The sweep is a setup input now, so a failure retries rather than degrades.

    The capability map it fills decides which modules get a buzzer. A
    background pass nobody waited for would build none of them and leave
    their records in the wrong repair card.
    """
    mock_client.resolve_records.side_effect = AmpioTimeoutError("no reply")

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_admin_records_never_seed_an_area(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    area_registry: ar.AreaRegistry,
) -> None:
    """No device takes an area from the admin-only Designer records.

    The sweep answers an administrator alone, and the Designer location is
    not the Home Assistant area map. Where a device belongs is the user's
    call, so the integration seeds nothing from the records.
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
    with_room = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(81)), mock_config_entry.entry_id
    )
    without_room = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(82)), mock_config_entry.entry_id
    )
    assert with_room is not None and without_room is not None
    sypialnia = area_registry.async_get_area_by_name("Sypialnia")
    assert sypialnia is not None
    assert with_room.area_id == sypialnia.id
    assert without_room.area_id is None


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
        elif entity.unique_id.startswith("module_"):
            assert entity.device_id == module.id
        else:
            assert entity.device_id is not None
            assert entity.device_id not in {hub.id, module.id}


async def test_server_objects_outrank_the_catalogue_on_the_mserv_row(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """The M-SERV's row comes from its objects, not from the module catalogue.

    Both tiers receive the server-owned objects; only the administrator
    tier receives the catalogue row. Reading the catalogue first would
    build one tree for an administrator and another for a restricted
    account wherever the two disagree.
    """
    mock_client.mserv = replace(mock_client.modules[1], id=42)
    mock_client.objects[99] = make_object(
        99, "przekaznik", 0, leaf_id="", id_urzadzenia=1, opis_menu="Pompa", state="0"
    )

    await setup_integration(hass, mock_config_entry)

    hub = device_registry.async_get_device_by_identifier(
        HUB_IDENTIFIER, mock_config_entry.entry_id
    )
    assert hub is not None
    child = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(99)), mock_config_entry.entry_id
    )
    assert child is not None
    assert child.parent_device_id == hub.id
    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, "module:1"), mock_config_entry.entry_id
        )
        is None
    )


async def test_remove_config_entry_device(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """The hub, live modules, and children under their resolved parent stay.

    A child whose object now resolves to another parent is deletable,
    because Home Assistant cannot move a child, and a delete is how the
    user moves it.
    """
    await setup_integration(hass, mock_config_entry)

    hub = device_registry.async_get_device_by_identifier(
        HUB_IDENTIFIER, mock_config_entry.entry_id
    )
    module_device = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    child = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(74)), mock_config_entry.entry_id
    )
    assert hub is not None
    assert module_device is not None
    assert child is not None

    assert not await async_remove_config_entry_device(hass, mock_config_entry, hub)
    assert not await async_remove_config_entry_device(
        hass, mock_config_entry, module_device
    )
    assert not await async_remove_config_entry_device(hass, mock_config_entry, child)

    # An object Designer no longer has leaves a stale child behind. Home
    # Assistant keeps such a device itself: its cleanup pass spares every
    # device that names a live config entry.
    stale = device_registry.async_get_or_create_child(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, unique_id(999))},
        parent_device_id=module_device.id,
        name="Gone",
    )
    assert await async_remove_config_entry_device(hass, mock_config_entry, stale)

    # Moved to another module row in Designer: the child still hangs under
    # module 17, so the user may delete it and let it come back.
    mock_client.objects[74] = replace(
        mock_client.objects[74], id_urzadzenia=3, leaf_id="0_be82_rel_0_1"
    )
    assert await async_remove_config_entry_device(hass, mock_config_entry, child)

    # Drop every object on the module: its device goes stale too.
    for object_id in [
        obj.id for obj in mock_client.objects.values() if obj.id_urzadzenia == 17
    ]:
        del mock_client.objects[object_id]
    assert await async_remove_config_entry_device(
        hass, mock_config_entry, module_device
    )


async def test_moved_object_is_repaired_by_a_delete(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    area_registry: ar.AreaRegistry,
) -> None:
    """A child keeps its parent, so a moved object needs a delete to follow.

    Home Assistant refuses to re-parent a child and skips the entity. The
    removal hook permits the delete, and the deleted record restores the
    device id, the area, and the name under the new module.
    """
    await setup_integration(hass, mock_config_entry)
    child = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(74)), mock_config_entry.entry_id
    )
    assert child is not None
    assert hass.states.get(pinned_id("switch", 74)).state == STATE_ON

    # What the user put on the device is what the delete has to give back.
    piwnica = area_registry.async_get_or_create("Piwnica")
    device_registry.async_update_child_device(
        child.id, name_by_user="Przekaznik piwnica", area_id=piwnica.id
    )

    mock_client.objects[74] = replace(
        mock_client.objects[74], id_urzadzenia=3, leaf_id="0_be82_rel_0_1"
    )
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # The entity is skipped, and its registry entry is left restored as
    # unavailable until the user deletes the device the object outgrew.
    assert hass.states.get(pinned_id("switch", 74)).state == STATE_UNAVAILABLE
    stuck = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(74)), mock_config_entry.entry_id
    )
    assert stuck is not None
    assert stuck.id == child.id
    assert await async_remove_config_entry_device(hass, mock_config_entry, stuck)

    device_registry.async_remove_device(stuck.id)
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    moved = device_registry.async_get_child_device_by_identifier(
        (DOMAIN, unique_id(74)), mock_config_entry.entry_id
    )
    new_module = device_registry.async_get_device_by_identifier(
        (DOMAIN, "module:3"), mock_config_entry.entry_id
    )
    assert moved is not None
    assert new_module is not None
    assert moved.id == child.id
    assert moved.parent_device_id == new_module.id
    assert moved.name_by_user == "Przekaznik piwnica"
    assert moved.area_id == piwnica.id
    assert hass.states.get(pinned_id("switch", 74)).state == STATE_ON
