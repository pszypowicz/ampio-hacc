"""The Ampio integration."""

from collections import Counter
import logging

from ampio_mqtt import (
    AccessTier,
    AmpioAuthError,
    AmpioClient,
    AmpioConnectionError,
    AmpioModule,
    AmpioObject,
    AmpioTimeoutError,
    AuthFailed,
    AvailabilityChanged,
    ConnectionDied,
)

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, issue_registry as ir

from .const import DOMAIN, PLATFORMS, STALE_RECORDS_ISSUE
from .data import AmpioConfigEntry, AmpioData
from .entity import HUB_IDENTIFIER, eligible_objects, module_identifier
from .stale import async_report_stale_records, live_identifiers

_LOGGER = logging.getLogger(__name__)


def _opt_str(value: object | None) -> str | None:
    """Stringify a catalogue field, passing None through."""
    return None if value is None else str(value)


def _module_name(module: AmpioModule | None, mac: int | None, module_id: int) -> str:
    """Name a module device: the installer's own name, the mac, or the row.

    ``nazwa_urzadzenia`` is the name the installer gave the module in Ampio
    Designer, and the module catalogue that carries it answers the
    administrator login alone. A restricted account is served the
    leaf-embedded mac instead, and a module whose objects all lost their
    leaf is left with its Designer row id. So this name follows the account
    tier. Nothing depends on it: ``AmpioEntity`` pins the entity id, so a
    name that changes on a tier switch renames the device in the interface
    and moves no id.
    """
    if module is not None and module.nazwa_urzadzenia:
        return module.nazwa_urzadzenia
    if mac is not None:
        return f"Ampio module 0x{mac:X}"
    return f"Ampio module {module_id}"


async def _async_sweep_records(client: AmpioClient) -> None:
    """Fill the admin-guarded record bundles, and log what the pass covered.

    The M-SERV answers one module at a time, so the pass runs as long as the
    install is large. Its result feeds the diagnostics download alone, and a
    module that stays silent costs that module's descriptions.
    """
    try:
        sweep = await client.resolve_records()
    except AmpioConnectionError, AmpioTimeoutError:
        _LOGGER.warning(
            "Could not resolve the Designer descriptions; "
            "the diagnostics download omits them"
        )
        return
    _LOGGER.debug(
        "The Designer description sweep read %d modules and %d stayed silent",
        len(sweep.answered_macs),
        len(sweep.silent_macs),
    )


async def async_setup_entry(hass: HomeAssistant, entry: AmpioConfigEntry) -> bool:
    """Set up Ampio from a config entry."""
    client = AmpioClient(
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    entry.async_on_unload(client.disconnect)

    # Home Assistant does not unload entries when it stops, so without this the
    # connection dies by task cancellation and is reported as a lost connection.
    async def _async_disconnect_client(event: Event) -> None:
        await client.disconnect()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_disconnect_client)
    )

    try:
        discovered = await client.connect()
    except AmpioAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        ) from err
    except AmpioConnectionError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="cannot_connect"
        ) from err
    # A True start() guarantees the server identity; the None check narrows the type.
    if not discovered or (info := client.server_info) is None:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="discovery_timeout"
        )
    # Every identity the integration writes is server-free, so a different
    # server answering at the stored host re-keys nothing. With one entry
    # allowed, it is a replacement or the user's own re-pointing, and the
    # entry takes the new server as its own.
    if info.server_key != entry.unique_id:
        _LOGGER.warning(
            "The Ampio server at %s reports mac %s, and this entry was set up "
            "with mac %s; taking the new server over",
            entry.data[CONF_HOST],
            info.server_key,
            entry.unique_id,
        )
        hass.config_entries.async_update_entry(entry, unique_id=info.server_key)

    # The hub is built from the server-info reply both account tiers receive.
    # Its name is the product name, because one M-SERV runs one install and
    # its catalogue row names it no better. The row decorates the model.
    device_registry = dr.async_get(hass)
    mserv = client.mserv
    hub = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={HUB_IDENTIFIER},
        manufacturer="Ampio",
        name="M-SERV",
        model=mserv.model if mserv and mserv.model else "M-SERV",
        sw_version=info.server_version,
        serial_number=info.device_id,
        configuration_url=f"http://{info.local_ip}" if info.local_ip else None,
    )

    # One device per Designer module row, registered before the platforms
    # load. The row id rides every object on both account tiers, so the tree
    # holds still across a tier change. The admin catalogue names the module
    # and decorates the model, the versions, and the serial; a restricted
    # account falls back to the leaf-embedded mac in the name. None of those
    # reaches an entity id.
    #
    # The M-SERV's own row is read off the objects that name it, because
    # both tiers receive those; the admin-only catalogue row answers only
    # when the account is served no server-owned object at all. Reading the
    # catalogue first would build one tree for an administrator and another
    # for a restricted account wherever the two disagree. A split vote goes
    # to the row most objects name, and ties to the first one seen.
    server_rows = Counter(
        obj.id_urzadzenia
        for obj in eligible_objects(client)
        if obj.is_server_owned and obj.id_urzadzenia is not None
    )
    mserv_id: int | None = None
    if server_rows:
        mserv_id = server_rows.most_common(1)[0][0]
    elif mserv is not None:
        mserv_id = mserv.id
    # One object per row stands for it: the first in catalogue order that
    # carries a leaf mac, which is both the mac that names the row and the
    # mac the catalogue join is gated on. A row whose objects have all lost
    # their leaf keeps the first object it saw and joins ungated.
    module_reps: dict[int, AmpioObject] = {}
    for obj in eligible_objects(client):
        module_id = obj.id_urzadzenia
        if obj.is_server_owned or module_id is None or module_id == mserv_id:
            continue
        rep = module_reps.get(module_id)
        if rep is None or (rep.module_mac is None and obj.module_mac is not None):
            module_reps[module_id] = obj
    module_device_ids: dict[int, str] = {}
    for module_id, rep in module_reps.items():
        # DB ids are volatile across a Designer resync while the leaf mac is
        # the hardware identity, so the library's join drops a row whose mac
        # disagrees with the leaf, and such a row decorates nothing.
        module = client.module_for(rep)
        module_device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={module_identifier(module_id)},
            name=_module_name(module, rep.module_mac, module_id),
            manufacturer="Ampio",
            via_device_id=hub.id,
            model=module.model if module else None,
            sw_version=_opt_str(module.wersja_softu) if module else None,
            hw_version=_opt_str(module.wersja_pcb) if module else None,
            serial_number=_opt_str(module.mac_global) if module else None,
        )
        module_device_ids[module_id] = module_device.id

    # The room map seeds each object child's area at its first creation,
    # and the diagnostics download carries it. Nothing in the entity or
    # device path depends on it after that, so a failure costs the seed and
    # must not fail setup.
    try:
        rooms = await client.fetch_rooms()
    except AmpioConnectionError:
        _LOGGER.warning(
            "Could not fetch the Ampio room map; the devices get no area suggestion"
        )
        rooms = {}

    # The description sweep fills each object's admin-guarded record bundle,
    # for the diagnostics download in the same way. It runs in the
    # background, because the M-SERV answers the requests one module at a
    # time and the pass therefore takes as long as the install is large.
    # Nothing waits on it: the platform partition reads
    # ``matter_device_type``, the catalogue column both tiers receive, which
    # the sweep never touches (docs/designer-quirks.md), and a record that
    # lands after an entity does reaches no id and no platform choice.
    if client.access_tier is AccessTier.ADMIN:
        entry.async_create_background_task(
            hass, _async_sweep_records(client), "ampio_resolve_records"
        )

    entry.runtime_data = AmpioData(client, hub.id, module_device_ids, rooms, mserv_id)

    was_unavailable = False

    @callback
    def _availability_changed(event: AvailabilityChanged) -> None:
        """Log a real outage once on loss and once on restore."""
        nonlocal was_unavailable
        if not event.available:
            was_unavailable = True
            _LOGGER.warning("Connection to the Ampio server lost; reconnecting")
        elif was_unavailable:
            was_unavailable = False
            _LOGGER.info("Connection to the Ampio server restored")

    @callback
    def _connection_ended(event: AuthFailed | ConnectionDied) -> None:
        """Recover from a terminal connection failure by re-running setup.

        Both events mean the library's reconnect loop has stopped for good;
        reloading re-raises a credential rejection as ConfigEntryAuthFailed
        and retries everything else with backoff.
        """
        _LOGGER.error(
            "Connection to the Ampio server ended (%s); reloading", event.reason
        )
        hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(
        client.subscribe(_availability_changed, of=AvailabilityChanged)
    )
    entry.async_on_unload(
        client.subscribe(_connection_ended, of=(AuthFailed, ConnectionDied))
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # The platforms have claimed every record they build. Whatever the
    # registries still hold for this entry beyond that is a leftover the
    # user gets to delete through one repair issue.
    async_report_stale_records(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AmpioConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: AmpioConfigEntry) -> None:
    """The records go with the entry, so the repair has nothing left to fix."""
    ir.async_delete_issue(hass, DOMAIN, STALE_RECORDS_ISSUE)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: AmpioConfigEntry, device_entry: dr.AnyDeviceEntry
) -> bool:
    """Allow removing a device whose object the account no longer receives.

    The child of an object that now resolves to another parent goes too.
    The hub always stays. A module device stays while the account still
    receives an object on it, and an object's child device stays while the
    account still receives that object and the child sits under the parent
    the object resolves to. The registry cannot re-parent a child, so the
    delete is how the user moves it, and the next reload builds it again
    under the resolved parent with its id, its area, and its name restored.
    """
    live, expected_parent = live_identifiers(entry.runtime_data)
    if isinstance(device_entry, dr.ChildDeviceEntry):
        # The registry cannot move a child, so a child whose object now
        # resolves elsewhere is deletable: the delete is the move.
        for identifier in device_entry.identifiers:
            if (
                identifier in expected_parent
                and expected_parent[identifier] != device_entry.parent_device_id
            ):
                return True
    return not any(identifier in live for identifier in device_entry.identifiers)
