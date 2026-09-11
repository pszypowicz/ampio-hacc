"""The Ampio integration."""

from functools import partial
import logging

from ampio_mqtt import (
    AccessTier,
    AmpioAuthError,
    AmpioClient,
    AmpioConnectionError,
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

from .const import ADMIN_ONLY_RECORDS_ISSUE, DOMAIN, PLATFORMS, STALE_RECORDS_ISSUE
from .data import AmpioConfigEntry, AmpioData, eligible_objects
from .stale import async_report_stale_records

_LOGGER = logging.getLogger(__name__)


async def _async_sweep_records(client: AmpioClient) -> None:
    """Fill the admin-guarded record bundles, and log what the pass covered.

    Two replies, the name table and the list, so the pass is one round trip
    each rather than a walk over the modules. Setup waits for it because the
    capability map it fills decides which modules carry a buzzer, and a
    platform that loaded first would build none of them.
    """
    try:
        sweep = await client.resolve_records()
    except (AmpioConnectionError, AmpioTimeoutError) as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="records_unavailable"
        ) from err
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

    entry.runtime_data = await AmpioData.async_create(hass, entry, client, info)

    # The subscription starts before the platforms load. An event that lands
    # while a platform is still loading queues its id like any other, and a
    # platform that registers later builds the new object in its own initial
    # pass. The one window is the loop turn between a platform's registration
    # and its initial add; a batch in that turn adds the same unique id, and
    # Home Assistant refuses the duplicate with a log line.
    entry.async_on_unload(entry.runtime_data.async_subscribe())

    # The sweep fills each module's capability map and each object's
    # record bundle. Setup waits for it: the capability map decides which
    # modules carry a buzzer, and the platforms load below.
    if client.access_tier is AccessTier.ADMIN:
        await _async_sweep_records(client)

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
    # user gets to delete through a repair issue, recomputed after every
    # catalogue change from here on.
    entry.runtime_data.async_mark_ready(
        partial(async_report_stale_records, hass, entry)
    )
    async_report_stale_records(hass, entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AmpioConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: AmpioConfigEntry) -> None:
    """The records go with the entry, so neither repair has anything left to fix."""
    ir.async_delete_issue(hass, DOMAIN, ADMIN_ONLY_RECORDS_ISSUE)
    ir.async_delete_issue(hass, DOMAIN, STALE_RECORDS_ISSUE)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: AmpioConfigEntry, device_entry: dr.AnyDeviceEntry
) -> bool:
    """Allow removing a device whose object the account no longer receives.

    The child of an object that now resolves to another parent goes too.
    The hub always stays. A module device stays while the account still
    receives an object on it, and an object's child device stays while the
    account still receives that object and the child sits under the parent
    the object resolves to. The batch the hook queues builds it again under
    the resolved parent within seconds, with its id, its area, and its name
    restored. A module device the hook permits to delete drops out of the
    tree, so that the next object on its row builds it back the same way.
    """
    data = entry.runtime_data
    live, expected_parent = data.live_identifiers()
    if isinstance(device_entry, dr.ChildDeviceEntry):
        # The registry cannot move a child, so a child whose object now
        # resolves elsewhere is deletable: the delete is the move, and the
        # batch queued here builds the child again under the new parent.
        for identifier in device_entry.identifiers:
            if (
                identifier in expected_parent
                and expected_parent[identifier] != device_entry.parent_device_id
            ):
                for obj in eligible_objects(data.client):
                    if (DOMAIN, obj.object_key) == identifier:
                        data.async_request_reconcile(obj)
                        break
                return True
    if any(identifier in live for identifier in device_entry.identifiers):
        return False
    # Home Assistant removes the device right after this returns. The tree
    # forgets a module device with it, so that the next batch on that row
    # builds the device back through the path that built it the first
    # time. A child's id keys no row.
    data.forget_module_device(device_entry.id)
    return True
