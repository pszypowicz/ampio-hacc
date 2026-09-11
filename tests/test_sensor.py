"""Tests for the Ampio sensor platform."""

from collections.abc import Generator
from dataclasses import replace
from unittest.mock import MagicMock, patch

from ampio_mqtt import (
    SENSOR_KIND_KEY_PREFIXES,
    SENSOR_KIND_KEYS,
    AmpioObject,
    AvailabilityChanged,
    ObjectRemoved,
    ObjectUpdated,
)
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.const import DOMAIN
from custom_components.ampio.sensor import SENSOR_DESCRIPTIONS, VALUE_KEY_PREFIX
from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from . import setup_integration
from .conftest import (
    HUB_IDENTIFIER,
    MSENS_IDENTIFIER,
    MSENS_ROW_NAME,
    emit,
    make_object,
    pinned_id,
    unique_id,
)

TEMPERATURE_ENTITY_ID = pinned_id("sensor", 36)
HUMIDITY_ENTITY_ID = pinned_id("sensor", 37)
CO2_ENTITY_ID = pinned_id("sensor", 43)
CURRENT_ENTITY_ID = pinned_id("sensor", 160)
ENERGY_ENTITY_ID = pinned_id("sensor", 161)
COUNTER_ENTITY_ID = pinned_id("sensor", 162)


@pytest.fixture(autouse=True)
def sensor_only() -> Generator[None]:
    """Limit setup to the sensor platform so snapshots stay platform-scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.SENSOR]):
        yield


async def _push_value(
    hass: HomeAssistant, client: MagicMock, oid: int, value: str
) -> None:
    """Replace the object's value in the store and push the update event."""
    obj = replace(client.objects[oid], state=value)
    client.objects[oid] = obj
    emit(client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()


def test_sensor_kind_vocabulary_is_mapped_or_excluded() -> None:
    """A library upgrade that adds a kind fails here instead of dropping entities.

    The metadata-less generic "value" kind is deliberately not exposed. Of
    the two open key families, the integer slots (``value_``) map to the
    value sensor and the unknown analog inputs (``analog_``) stay excluded;
    a new key or prefix forces a mapping decision.
    """
    assert SENSOR_KIND_KEYS - {"value"} == SENSOR_DESCRIPTIONS.keys()
    assert VALUE_KEY_PREFIX in SENSOR_KIND_KEY_PREFIXES
    assert set(SENSOR_KIND_KEY_PREFIXES) - {VALUE_KEY_PREFIX} == {"analog_"}


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


@pytest.mark.usefixtures("mock_client")
async def test_devices(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot every device registry entry the integration creates."""
    await setup_integration(hass, mock_config_entry)

    devices = dr.async_entries_for_config_entry(
        device_registry, mock_config_entry.entry_id
    )
    assert devices
    for device in devices:
        assert device == snapshot(name=f"device-{device.name}")


async def test_push_update_changes_state(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A pushed object update is reflected in the entity state."""
    await setup_integration(hass, mock_config_entry)

    await _push_value(hass, mock_client, 36, "25.5")

    assert hass.states.get(TEMPERATURE_ENTITY_ID).state == "25.5"


async def test_unusable_value_surfaces_as_unknown(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A push a numeric sensor cannot represent maps to unknown, not an error.

    Which value shapes parse to None (nan, inf, overflow, non-numeric) is the
    library's ``numeric_value`` contract, covered by its own tests; here one
    representative proves the None-to-unknown mapping.
    """
    await setup_integration(hass, mock_config_entry)

    await _push_value(hass, mock_client, 36, "INVALID")

    assert hass.states.get(TEMPERATURE_ENTITY_ID).state == STATE_UNKNOWN


@pytest.mark.usefixtures("mock_client")
async def test_value_sensor_reads_the_designer_unit(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """An integer slot carries the unit, class, state class, and precision Designer stores."""
    await setup_integration(hass, mock_config_entry)

    current = hass.states.get(CURRENT_ENTITY_ID)
    assert current is not None
    assert current.state == "0.37"
    assert current.attributes["unit_of_measurement"] == "A"
    assert current.attributes["device_class"] == "current"
    assert current.attributes["state_class"] == "measurement"
    entry = entity_registry.async_get(CURRENT_ENTITY_ID)
    assert entry is not None
    assert entry.options["sensor"]["suggested_display_precision"] == 3

    energy = hass.states.get(ENERGY_ENTITY_ID)
    assert energy is not None
    assert energy.attributes["unit_of_measurement"] == "kWh"
    assert energy.attributes["device_class"] == "energy"
    assert energy.attributes["state_class"] == "total_increasing"


@pytest.mark.usefixtures("mock_client")
async def test_value_sensor_without_unit_is_a_plain_number(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A slot with neither a Unit field nor a format tail keeps its value and no state class."""
    await setup_integration(hass, mock_config_entry)

    counter = hass.states.get(COUNTER_ENTITY_ID)
    assert counter is not None
    assert counter.state == "42.0"
    assert "unit_of_measurement" not in counter.attributes
    assert "device_class" not in counter.attributes
    assert "state_class" not in counter.attributes


async def test_value_sensor_follows_a_unit_change(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A Designer edit of the unit re-classifies the entity on its next write."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[162], url="V")
    mock_client.objects[162] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    counter = hass.states.get(COUNTER_ENTITY_ID)
    assert counter is not None
    assert counter.attributes["unit_of_measurement"] == "V"
    assert counter.attributes["device_class"] == "voltage"
    assert counter.attributes["state_class"] == "measurement"


async def test_value_sensor_non_numeric_push_reads_unknown(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A push the slot cannot represent as a number maps to unknown, not an error."""
    await setup_integration(hass, mock_config_entry)

    await _push_value(hass, mock_client, 160, "INVALID")

    assert hass.states.get(CURRENT_ENTITY_ID).state == STATE_UNKNOWN


async def test_push_only_updates_target_entity(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A push for one object must not write state on its siblings.

    ``last_reported`` advances on any write (even an unchanged value), so it
    is the signal that catches a spurious fan-out.
    """
    await setup_integration(hass, mock_config_entry)
    temperature_before = hass.states.get(TEMPERATURE_ENTITY_ID).last_reported

    await _push_value(hass, mock_client, 37, "45.5")

    assert hass.states.get(HUMIDITY_ENTITY_ID).state == "45.5"
    assert hass.states.get(TEMPERATURE_ENTITY_ID).last_reported == temperature_before


async def test_removed_object_becomes_unavailable(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An object evicted from the catalogue flips its entity unavailable."""
    await setup_integration(hass, mock_config_entry)

    obj = mock_client.objects.pop(43)
    emit(mock_client, ObjectRemoved(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(CO2_ENTITY_ID).state == STATE_UNAVAILABLE


async def test_hidden_object_becomes_unavailable(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A Designer delete keeps the row with the hidden bit set on the admin tier."""
    await setup_integration(hass, mock_config_entry)

    obj = replace(mock_client.objects[43], params=16)
    mock_client.objects[43] = obj
    emit(mock_client, ObjectUpdated(object=obj))
    await hass.async_block_till_done()

    assert hass.states.get(CO2_ENTITY_ID).state == STATE_UNAVAILABLE


async def test_broker_availability_flips_entities(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A broker disconnect flips every entity unavailable; reconnect restores."""
    await setup_integration(hass, mock_config_entry)

    mock_client.available = False
    emit(mock_client, AvailabilityChanged(available=False))
    await hass.async_block_till_done()

    assert hass.states.get(TEMPERATURE_ENTITY_ID).state == STATE_UNAVAILABLE
    assert hass.states.get(HUMIDITY_ENTITY_ID).state == STATE_UNAVAILABLE
    assert hass.states.get(CO2_ENTITY_ID).state == STATE_UNAVAILABLE

    mock_client.available = True
    emit(mock_client, AvailabilityChanged(available=True))
    await hass.async_block_till_done()

    assert hass.states.get(TEMPERATURE_ENTITY_ID).state == "24.4"


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param(
            make_object(
                200,
                "lin_wej",
                1,
                leaf_id="",
                funkcja=5,
                opis_menu="Ghost",
                state="55.0",
                params=16,
            ),
            id="hidden-ghost-without-leaf",
        ),
        pytest.param(
            make_object(
                201, "lin_wej", 7, leaf_id="0_cb8f_lin_0_9", funkcja=6, params=16
            ),
            id="hidden",
        ),
        pytest.param(
            make_object(
                202,
                "lin_wej",
                9,
                leaf_id="0_cb8f_lin_0_10",
                funkcja=7,
                opis_menu="Status",
                state="42.0",
            ),
            id="kind-without-description",
        ),
        pytest.param(
            make_object(
                203,
                "przekaznik",
                0,
                leaf_id="0_cb8f_prz_0_1",
                funkcja=8,
                opis_menu="Relay",
                state="1",
            ),
            id="not-a-sensor",
        ),
    ],
)
async def test_unexposable_objects_are_skipped(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    extra: AmpioObject,
) -> None:
    """Invisible objects and kinds outside the sensor table produce no entity."""
    mock_client.objects[extra.id] = extra

    await setup_integration(hass, mock_config_entry)

    entities = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    assert len(entities) == 13


@pytest.mark.parametrize(
    ("leaf_id", "id_urzadzenia"),
    [
        # A row naming no module has no module device to hang under.
        pytest.param("0_nomac_temp_0_1", None, id="no-module-row"),
        # The M-SERV's own leaf outranks whatever row the object carries.
        pytest.param("0_1_temp_0_1", 17, id="server-owned"),
    ],
)
async def test_hub_anchored_objects(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
    leaf_id: str,
    id_urzadzenia: int | None,
) -> None:
    """The M-SERV's own objects and rows naming no module get a child of the hub."""
    mock_client.objects[500] = make_object(
        500,
        "temp",
        1,
        leaf_id=leaf_id,
        id_urzadzenia=id_urzadzenia,
        funkcja=5,
        opis_menu="Hub sensor",
    )

    await setup_integration(hass, mock_config_entry)

    hub = device_registry.async_get_device_by_identifier(
        HUB_IDENTIFIER, mock_config_entry.entry_id
    )
    assert hub is not None
    entity_id = entity_registry.async_get_entity_id(
        Platform.SENSOR, DOMAIN, unique_id(500)
    )
    assert entity_id is not None
    entity_entry = entity_registry.async_get(entity_id)
    assert entity_entry is not None
    assert entity_entry.device_id is not None
    child = device_registry.async_get(entity_entry.device_id)
    assert isinstance(child, dr.ChildDeviceEntry)
    assert child.parent_device_id == hub.id


async def test_module_without_catalogue_row_gets_bare_device(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A module row the catalogue does not list still keys its own device."""
    mock_client.objects[500] = make_object(
        500,
        "temp",
        1,
        leaf_id="0_dead_temp_0_1",
        id_urzadzenia=99,
        opis_menu="Dangling",
    )

    await setup_integration(hass, mock_config_entry)

    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, "module:99"), mock_config_entry.entry_id
    )
    assert device is not None
    assert device.name == "Ampio module 99"
    assert device.model is None
    entity_id = entity_registry.async_get_entity_id(
        Platform.SENSOR, DOMAIN, unique_id(500)
    )
    assert entity_id is not None
    entity_entry = entity_registry.async_get(entity_id)
    assert entity_entry is not None
    assert entity_entry.device_id is not None
    child = device_registry.async_get(entity_entry.device_id)
    assert isinstance(child, dr.ChildDeviceEntry)
    assert child.parent_device_id == device.id


@pytest.mark.parametrize(
    ("changes", "expected_name", "expected_model"),
    [
        pytest.param(
            {"nazwa_urzadzenia": None},
            MSENS_ROW_NAME,
            "M-SENS",
            id="nameless-module",
        ),
        # The catalogue row joins on the row id alone, so its name and
        # model apply whatever address it carries.
        pytest.param({"mac": 99999}, "m-sens salon", "M-SENS", id="disagreeing-mac"),
    ],
)
async def test_module_name_follows_the_catalogue(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    changes: dict[str, int | None],
    expected_name: str,
    expected_model: str | None,
) -> None:
    """A nameless row falls back to its row id."""
    mock_client.modules[17] = replace(mock_client.modules[17], **changes)

    await setup_integration(hass, mock_config_entry)

    device = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert device is not None
    assert device.name == expected_name
    assert device.model == expected_model


@pytest.mark.usefixtures("sensor_only")
async def test_module_row_missing_from_the_catalogue(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A visible object on a row the catalogue lacks still gets its device.

    Ampio Designer deletes a device at once and only unassigns its objects
    into a collapsed UNGROUPED section, so a visible object can point at a
    row that no longer exists. The device takes the row id and no
    decoration, rather than costing the whole setup.
    """
    del mock_client.modules[17]

    await setup_integration(hass, mock_config_entry)

    module = device_registry.async_get_device_by_identifier(
        MSENS_IDENTIFIER, mock_config_entry.entry_id
    )
    assert module is not None
    assert module.name == MSENS_ROW_NAME
    assert module.model is None
    assert module.serial_number is None


async def test_pulse_time_diagnostic(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """The Designer time surfaces as a diagnostic sensor where writes honor it.

    A timed RGBW gets none (set_colors has no timed form), a timed cover
    gets none (czas is the travel time there), and a timeless bell gets
    none.
    """
    for oid in (72, 82):
        mock_client.objects[oid] = replace(mock_client.objects[oid], czas=500)
    await setup_integration(hass, mock_config_entry)

    entry = entity_registry.async_get(pinned_id("sensor", 150, "_pulse"))
    assert entry is not None
    assert entry.entity_category is EntityCategory.DIAGNOSTIC
    state = hass.states.get(pinned_id("sensor", 150, "_pulse"))
    assert state is not None
    assert float(state.state) == 3.0

    for object_id in (149, 72, 82):
        assert (
            entity_registry.async_get_entity_id(
                "sensor", DOMAIN, unique_id(object_id, "_pulse")
            )
            is None
        )
