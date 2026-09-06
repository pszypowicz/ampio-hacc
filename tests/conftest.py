"""Fixtures for the Ampio integration tests.

The library client is mocked at the integration boundary and seeded with
real ``ampio_mqtt`` model instances, so tests drive the integration through
the same public surface the library exposes: the state properties and the
``subscribe`` event stream (dispatched via :func:`emit`).
"""

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

from ampio_mqtt import (
    AccessTier,
    AmpioModule,
    AmpioObject,
    AmpioScene,
    AmpioServerInfo,
    RecordSweep,
    ThermostatState,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion

from custom_components.ampio.const import DOMAIN
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the Ampio custom component in every test."""


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Pin the snapshot fixture to the Home Assistant extension.

    The plugin-delivered override in pytest-homeassistant-custom-component
    only wins when it registers after syrupy, and plugin registration order
    is not deterministic across environments. A conftest fixture takes
    precedence over both plugins, so the Home Assistant serializer and the
    snapshots directory apply everywhere.
    """
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


MSERV_MAC = "47846"
# Identifiers carry no server mac: the hub is one constant, a module is its
# leaf-embedded mac, an object is its Designer id.
HUB_IDENTIFIER = (DOMAIN, "hub")
MSENS_IDENTIFIER = (DOMAIN, "module:52111")
# The module device is named from the admin-only module catalogue, and it
# falls back to the leaf-embedded mac that both account tiers receive.
MSENS_DEVICE_NAME = "m-sens salon"
MSENS_MAC_NAME = "Ampio module 0xCB8F"


def unique_id(oid: int, suffix: str = "") -> str:
    """The unique id of an object's entity: the object key alone."""
    return f"obj_{oid}{suffix}"


def pinned_id(domain: str, oid: int, suffix: str = "") -> str:
    """The pinned entity id of an object's entity in ``domain``.

    The integration carries this id into the add, so no device name and no
    area name compose it. It is the unique id with the domain in front.
    """
    return f"{domain}.ampio_{unique_id(oid, suffix)}"


# A sweep that read every module and joined nothing.
EMPTY_SWEEP = RecordSweep(
    records={}, answered_macs=frozenset(), silent_macs=frozenset()
)

USER_INPUT = {
    CONF_HOST: "ampio.test",
    CONF_USERNAME: "user",
    CONF_PASSWORD: "pass",
}

# The identity reply of the default test server.
SERVER_INFO = AmpioServerInfo(
    mac=47846,
    user_id=-1,
    server_version="1865",
    server_revision="409",
    mqtt_version="5.133.11",
    local_ip="10.0.0.1",
    device_id="0011223344556677",
)


def make_object(
    oid: int,
    typ: str,
    interpretacja: int,
    *,
    leaf_id: str,
    id_urzadzenia: int | None = 17,
    funkcja: int = 1,
    opis_menu: str | None = None,
    state: str | None = None,
    params: int = 0,
    matter_device_type: int | None = None,
    lammel: int | None = None,
    thermostat: ThermostatState | None = None,
    pulse_ms: int = 0,
) -> AmpioObject:
    """Build a classified object the way discovery would."""
    return AmpioObject(
        id=oid,
        id_urzadzenia=id_urzadzenia,
        typ_komponentu=typ,
        opis_menu=opis_menu,
        interpretacja=interpretacja,
        funkcja=funkcja,
        leaf_id=leaf_id,
        params=params,
        state=state,
        matter_device_type=matter_device_type,
        lammel=lammel,
        thermostat=thermostat,
        pulse_ms=pulse_ms,
    )


# The default object catalogue: one visible sensor per supported kind on
# module 17, so the entity snapshot pins every description's device class,
# unit, precision, and display name. The hidden phantom mirrors a real M-SENS
# where adding a CO2 object in Designer leaves an unnamed stub sharing the
# leafId behind; the ghost is a removed-but-still-returned row, hidden bit
# set and no leafId. The three input objects (a named flag, a system-typed
# detection row that must never surface, a named wired-button input) feed
# the binary_sensor and switch platforms the same way.
# The four output objects (a named dimmer, an rgbw, a Matter-tagged relay
# light, an untagged relay for the switch platform) feed the light and
# switch platforms, plus a plug-tagged relay for the switch platform's
# outlet class. The three cover objects (a
# plain roleta without feedback, a percent roleta, a lamella blind) feed the
# cover platform. The thermostat object feeds the climate platform; its value
# is the running flag. The named flag on module mac 1 (the M-SERV itself) is
# server-owned, so its child device parents to the hub instead of a module
# device like every other module-owned object's child.
DEFAULT_OBJECTS = (
    make_object(
        36,
        "temp",
        1,
        leaf_id="0_cb8f_temp_0_1",
        opis_menu="Temperatura",
        state="24.4",
    ),
    make_object(
        37,
        "lin_wej",
        1,
        leaf_id="0_cb8f_lin_0_2",
        funkcja=2,
        opis_menu="Wilgotność",
        state="42.000000",
    ),
    make_object(43, "lin_wej", 7, leaf_id="0_cb8f_lin_0_3", funkcja=3, state="900.5"),
    make_object(44, "lin_wej", 2, leaf_id="0_cb8f_lin_0_4", funkcja=5, state="1013.2"),
    make_object(45, "lin_wej", 6, leaf_id="0_cb8f_lin_0_5", funkcja=6, state="1019.7"),
    make_object(46, "lin_wej", 3, leaf_id="0_cb8f_lin_0_6", funkcja=7, state="38.5"),
    make_object(47, "lin_wej", 4, leaf_id="0_cb8f_lin_0_7", funkcja=8, state="742"),
    make_object(48, "lin_wej", 5, leaf_id="0_cb8f_lin_0_8", funkcja=9, state="23"),
    make_object(
        61,
        "flaga",
        0,
        leaf_id="0_cb8f_flaga_0_1",
        opis_menu="Podlewanie",
        state="1",
    ),
    # The two bell-marked objects (params bit 15): a named relay and an
    # unnamed flag, the button platform's populations.
    make_object(
        149,
        "flaga",
        0,
        leaf_id="0_cb8f_flaga_0_3",
        funkcja=12,
        state="0",
        params=1 << 15,
    ),
    make_object(
        150,
        "przekaznik",
        0,
        leaf_id="0_cb8f_rel_0_6",
        funkcja=13,
        opis_menu="Dzwonek",
        state="0",
        params=1 << 15,
        pulse_ms=3000,
    ),
    # A second Designer view of the output the previous object drives.
    # Designer lets one output carry several views, and every view repeats
    # the leafId, so this pair differs in the database id alone.
    make_object(
        151,
        "przekaznik",
        0,
        leaf_id="0_cb8f_rel_0_6",
        funkcja=13,
        opis_menu="Dzwonek",
        state="0",
        params=1 << 15,
        pulse_ms=3000,
    ),
    make_object(62, "detekcja", 0, leaf_id="0_cb8f_det_0_2", funkcja=2, state="0"),
    make_object(
        146,
        "wej",
        15,
        leaf_id="0_cb8f_wej_0_9",
        opis_menu="Przycisk kino",
        state="0",
    ),
    make_object(
        71,
        "led",
        0,
        leaf_id="0_cb8f_led_0_1",
        funkcja=3,
        opis_menu="Taras LED",
        state="128",
    ),
    make_object(
        72,
        "rgbw",
        0,
        leaf_id="0_cb8f_rgbw_0_2",
        funkcja=4,
        opis_menu="Salon RGBW",
        state=str(60 | 120 << 8 | 180 << 16 | 240 << 24),
    ),
    make_object(
        73,
        "przekaznik",
        0,
        leaf_id="0_cb8f_rel_0_3",
        funkcja=5,
        opis_menu="Kinkiet",
        state="0",
        matter_device_type=0x0100,
    ),
    make_object(74, "przekaznik", 0, leaf_id="0_cb8f_rel_0_4", funkcja=6, state="1"),
    make_object(
        75,
        "przekaznik",
        0,
        leaf_id="0_cb8f_rel_0_5",
        funkcja=10,
        opis_menu="Gniazdo Taras",
        state="0",
        matter_device_type=0x010A,
    ),
    make_object(
        81,
        "roleta",
        0,
        leaf_id="0_cb8f_rol_0_1",
        funkcja=7,
        opis_menu="Roleta Sypialnia",
    ),
    make_object(
        82,
        "roleta_procenty",
        0,
        leaf_id="0_cb8f_rolp_0_2",
        funkcja=8,
        opis_menu="Roleta Kuchnia",
        state="35",
    ),
    make_object(
        83,
        "roleta_lamelki",
        0,
        leaf_id="0_cb8f_roll_0_3",
        funkcja=9,
        opis_menu="Zaluzja Goscinny",
        state="70",
        lammel=40,
    ),
    make_object(
        91,
        "reg",
        0,
        leaf_id="0_cb8f_reg_0_1",
        funkcja=11,
        opis_menu="Termostat Salon",
        state="1",
        thermostat=ThermostatState(
            measure_temp=21.8,
            set_temperature=22.5,
            mode="S",
            cooling=False,
        ),
    ),
    make_object(132, "lin_wej", 7, leaf_id="0_cb8f_lin_0_3", funkcja=3, params=16),
    make_object(99, "lin_wej", 2, leaf_id="", funkcja=4, params=16),
    make_object(
        121,
        "flaga",
        0,
        leaf_id="0_1_flaga_0_9",
        id_urzadzenia=1,
        opis_menu="Dom pusty",
        state="0",
    ),
)

# The default module catalogue an administrator account receives.
DEFAULT_MODULES = (
    AmpioModule(
        id=17,
        mac=52111,
        mac_global=152111,
        nazwa_urzadzenia="m-sens salon",
        typ_urzadzenia=44,
        wersja_softu=63,
        wersja_pcb=7,
    ),
    AmpioModule(
        id=3,
        mac=48770,
        nazwa_urzadzenia="MREL 3",
        typ_urzadzenia=4,
        wersja_softu=11000,
        wersja_pcb=2,
    ),
    AmpioModule(
        id=1,
        mac=1,
        mac_global=47846,
        nazwa_urzadzenia="MSERV",
        typ_urzadzenia=10,
        wersja_softu=11639,
        wersja_pcb=7,
    ),
)

# The scene catalogue the mocked fetch returns: one enabled scene the
# platform exposes and one disabled scene it must skip.
DEFAULT_SCENES = (
    AmpioScene(id=5, scene_name="Wieczór", active=True, object_ids=frozenset({71, 72})),
    AmpioScene(id=6, scene_name="Nieaktywna", active=False),
)

# The room map the mocked fetch returns: a named sensor, a light, a cover,
# and the server-owned flag get rooms; every other object stays roomless on
# purpose.
DEFAULT_ROOMS = {36: "Salon", 71: "Taras", 81: "Sypialnia", 121: "Techniczne"}


def emit(client: MagicMock, event: Any) -> None:
    """Dispatch ``event`` to the live listeners subscribed on the mocked client.

    Iterates a snapshot: a listener whose dispatch triggers new
    subscriptions (a reload re-running setup) must not receive this event
    on its replacement registrations too.
    """
    for listener, of, object_id in list(client.live_subscriptions):
        if not isinstance(event, of):
            continue
        if object_id is not None and event.object.id != object_id:
            continue
        listener(event)


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=USER_INPUT[CONF_HOST],
        data=USER_INPUT,
        unique_id=MSERV_MAC,
    )


@pytest.fixture
def mock_client_class() -> Generator[MagicMock]:
    """Patch AmpioClient with a connected, discovery-complete mock."""
    with (
        patch("custom_components.ampio.AmpioClient", autospec=True) as client_class,
        patch("custom_components.ampio.config_flow.AmpioClient", new=client_class),
    ):
        client_class.check_connection.return_value = SERVER_INFO
        client = client_class.return_value
        client.connect.return_value = True
        client.available = True
        client.objects = {obj.id: obj for obj in DEFAULT_OBJECTS}
        client.modules = {module.id: module for module in DEFAULT_MODULES}
        client.server_info = SERVER_INFO
        client.mserv = client.modules[1]
        client.access_tier = AccessTier.ADMIN
        client.fetch_scenes.return_value = list(DEFAULT_SCENES)
        client.fetch_rooms.return_value = dict(DEFAULT_ROOMS)
        client.resolve_records.return_value = EMPTY_SWEEP

        # Mirrors the real resolver's documented contract over the seeded
        # catalogue: join by device_id, gated on the leaf-derived mac.
        def module_for(obj: AmpioObject) -> AmpioModule | None:
            if obj.id_urzadzenia is None:
                return None
            module = client.modules.get(obj.id_urzadzenia)
            if module is None or module.mac is None or module.mac != obj.module_mac:
                return None
            return module

        client.module_for.side_effect = module_for

        # Track live registrations so unsubscribing works: emit() must not
        # reach listeners from a torn-down setup. Unsubscribing is idempotent,
        # matching the real client's documented contract.
        subscriptions: list[tuple[Any, type | tuple[type, ...], int | None]] = []

        def subscribe(
            listener: Any,
            *,
            of: type | tuple[type, ...],
            object_id: int | None = None,
        ) -> Any:
            registration = (listener, of, object_id)
            subscriptions.append(registration)

            def unsubscribe() -> None:
                if registration in subscriptions:
                    subscriptions.remove(registration)

            return unsubscribe

        client.subscribe.side_effect = subscribe
        client.live_subscriptions = subscriptions
        yield client_class


@pytest.fixture
def mock_client(mock_client_class: MagicMock) -> MagicMock:
    """The mocked AmpioClient instance the integration runs on."""
    return mock_client_class.return_value


@pytest.fixture
def mock_setup_entry() -> Generator[MagicMock]:
    """Patch the entry setup so config-flow tests don't run real setup."""
    with patch("custom_components.ampio.async_setup_entry", return_value=True) as mock:
        yield mock
