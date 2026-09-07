"""Runtime data for the Ampio integration: the device tree the catalogue defines."""

from collections import Counter
from collections.abc import Iterator
import logging
from typing import Final

from ampio_mqtt import (
    AmpioClient,
    AmpioConnectionError,
    AmpioModule,
    AmpioObject,
    AmpioServerInfo,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# The registry identifiers carry no server mac. Object ids live in the
# Designer database, which moves to new hardware with the project; the
# server mac does not. One M-SERV per Home Assistant keeps them unique.
HUB_IDENTIFIER: Final = (DOMAIN, "hub")


def module_identifier(module_id: int) -> tuple[str, str]:
    """The registry identifier of the module device for Designer row ``module_id``."""
    return (DOMAIN, f"module:{module_id}")


def eligible_objects(client: AmpioClient) -> Iterator[AmpioObject]:
    """The objects any platform may expose as entities.

    ``visible`` is the M-SERV's own predicate for what the user still sees
    in Ampio Designer: the hidden bit alone. A row without a ``leaf_id`` is
    still an object, because Designer clears that field when an object's
    Matter box is unchecked. ``is_system`` then holds back the M-SERV's
    own detection and simulation objects, which no platform covers.
    """
    return (obj for obj in client.objects.values() if obj.visible and not obj.is_system)


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


class AmpioData:
    """Runtime data for one Ampio server: the device tree the catalogue defines.

    ``async_create`` builds the hub, one module device per Designer module
    row, and the room map. ``ensure_module_device`` creates the module
    device of a row the tree meets later, through the same path.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: AmpioConfigEntry,
        client: AmpioClient,
        hub_device_id: str,
        mserv_id: int | None,
    ) -> None:
        """Hold the tree's fixed points; the module devices and rooms fill in after."""
        self.hass = hass
        self.entry = entry
        self.client = client
        # Registry ids the object child devices parent to: the hub, and one
        # module device per Designer module row.
        self.hub_device_id = hub_device_id
        self.module_device_ids: dict[int, str] = {}
        # The app room of each object, from the tier-shared room tables. It
        # seeds a child device's area once, at the device's first creation.
        self.rooms: dict[int, str] = {}
        # The Designer row of the M-SERV itself. Its objects sit on the hub.
        self.mserv_id = mserv_id

    @classmethod
    async def async_create(
        cls,
        hass: HomeAssistant,
        entry: AmpioConfigEntry,
        client: AmpioClient,
        info: AmpioServerInfo,
    ) -> AmpioData:
        """Build the tree for the catalogue as it stands after discovery."""
        # The hub is built from the server-info reply both account tiers
        # receive. Its name is the product name, because one M-SERV runs one
        # install and its catalogue row names it no better. The row
        # decorates the model.
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

        # The M-SERV's own row is read off the objects that name it, because
        # both tiers receive those; the admin-only catalogue row answers only
        # when the account is served no server-owned object at all. Reading
        # the catalogue first would build one tree for an administrator and
        # another for a restricted account wherever the two disagree. A split
        # vote goes to the row most objects name, and ties to the first one
        # seen.
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
        data = cls(hass, entry, client, hub.id, mserv_id)

        # One device per Designer module row, registered before the
        # platforms load. One object per row stands for it: the first in
        # catalogue order that carries a leaf mac, which is both the mac that
        # names the row and the mac the catalogue join is gated on. A row
        # whose objects have all lost their leaf keeps the first object it
        # saw and joins ungated.
        module_reps: dict[int, AmpioObject] = {}
        for obj in eligible_objects(client):
            module_id = obj.id_urzadzenia
            if obj.is_server_owned or module_id is None or module_id == mserv_id:
                continue
            rep = module_reps.get(module_id)
            if rep is None or (rep.module_mac is None and obj.module_mac is not None):
                module_reps[module_id] = obj
        for rep in module_reps.values():
            data.ensure_module_device(rep)

        # The room map seeds each object child's area at its first creation,
        # and the diagnostics download carries it. Nothing in the entity or
        # device path depends on it after that, so a failure costs the seed
        # and must not fail setup.
        try:
            data.rooms = await client.fetch_rooms()
        except AmpioConnectionError:
            _LOGGER.warning(
                "Could not fetch the Ampio room map; the devices get no area suggestion"
            )
        return data

    @callback
    def ensure_module_device(self, obj: AmpioObject) -> None:
        """Create the module device of the object's row, unless it exists.

        The row id rides every object on both account tiers, so the tree
        holds still across a tier change. The admin catalogue names the
        module and decorates the model, the versions, and the serial; a
        restricted account falls back to the leaf-embedded mac in the name.
        None of those reaches an entity id. DB ids are volatile across a
        Designer resync while the leaf mac is the hardware identity, so the
        library's join drops a row whose mac disagrees with the leaf, and
        such a row decorates nothing.
        """
        module_id = obj.id_urzadzenia
        if (
            obj.is_server_owned
            or module_id is None
            or module_id == self.mserv_id
            or module_id in self.module_device_ids
        ):
            return
        module = self.client.module_for(obj)
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={module_identifier(module_id)},
            name=_module_name(module, obj.module_mac, module_id),
            manufacturer="Ampio",
            via_device_id=self.hub_device_id,
            model=module.model if module else None,
            sw_version=_opt_str(module.wersja_softu) if module else None,
            hw_version=_opt_str(module.wersja_pcb) if module else None,
            serial_number=_opt_str(module.mac_global) if module else None,
        )
        self.module_device_ids[module_id] = device.id

    def parent_for(self, obj: AmpioObject) -> str:
        """The device an object's child hangs under: its module, or the hub.

        The Designer module row id rides every object row on both account
        tiers, leaf or no leaf, so the tree never depends on the leaf id. The
        M-SERV's own objects sit on the hub.
        """
        module_id = obj.id_urzadzenia
        if obj.is_server_owned or module_id is None or module_id == self.mserv_id:
            return self.hub_device_id
        return self.module_device_ids.get(module_id, self.hub_device_id)

    def live_identifiers(
        self,
    ) -> tuple[set[tuple[str, str]], dict[tuple[str, str], str]]:
        """The device identifiers the catalogue keeps, and each child's parent.

        The hub is always live. A module device is live while an eligible
        object resolves to it, and an object's child is live while the object
        is eligible. The parent map says where each child belongs now, which
        is what a moved object's stuck child is compared against.
        """
        live: set[tuple[str, str]] = {HUB_IDENTIFIER}
        expected_parent: dict[tuple[str, str], str] = {}
        for obj in eligible_objects(self.client):
            parent = self.parent_for(obj)
            if parent != self.hub_device_id and obj.id_urzadzenia is not None:
                live.add(module_identifier(obj.id_urzadzenia))
            live.add((DOMAIN, obj.object_key))
            expected_parent[(DOMAIN, obj.object_key)] = parent
        return live, expected_parent


type AmpioConfigEntry = ConfigEntry[AmpioData]
