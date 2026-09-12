"""Runtime data for the Ampio integration: the device tree the catalogue defines."""

import asyncio
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
import logging
from typing import Final

from ampio_mqtt import (
    AccessTier,
    AmpioClient,
    AmpioConnectionError,
    AmpioModule,
    AmpioObject,
    AmpioServerInfo,
    ObjectRemoved,
    ObjectUpdated,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    EntityPlatform,
    async_get_current_platform,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# The registry identifiers carry no server mac. Object ids live in the
# Designer database, which moves to new hardware with the project; the
# server mac does not. One M-SERV per Home Assistant keeps them unique.
HUB_IDENTIFIER: Final = (DOMAIN, "hub")

# The M-SERV pushes the catalogue and the params table as separate messages
# within one second of a Designer save, so a batch waits this long for both.
RECONCILE_COOLDOWN: Final = 1.0

type Fingerprint = tuple[str | None, int | None, int | None, int, int, int | None]


def fingerprint(obj: AmpioObject) -> Fingerprint:
    """The catalogue fields that decide an object's platforms and its parent.

    A state push changes none of them, and neither does a rename, because
    no name composes an id. The leaf id stays out on purpose: Designer
    clears it on a Matter uncheck, and that uncheck must move nothing. The
    one leaf-derived fact the tree reads, ``is_server_owned`` in
    ``parent_for``, is therefore left out as well, so a leaf change alone
    queues no batch.
    """
    return (
        obj.typ_komponentu,
        obj.interpretacja,
        obj.matter_device_type,
        obj.params,
        obj.czas,
        obj.id_urzadzenia,
    )


def _built_entities(platform: EntityPlatform, object_key: str) -> dict[str, Entity]:
    """The entities a platform holds for an object, keyed by unique id.

    The unique id is the object key, or the object key and a suffix such as
    ``_pulse``. The platform's own table is the truth for what is built, so
    nothing shadows it.
    """
    prefix = f"{object_key}_"
    return {
        uid: entity
        for entity in platform.entities.values()
        if (uid := entity.unique_id) is not None
        and (uid == object_key or uid.startswith(prefix))
    }


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


# What a platform builds for one object: the entities, or nothing. The
# platform module owns the partition; the runtime data owns when it runs.
type EntityFactory = Callable[[AmpioData, AmpioObject], Iterable[Entity]]


@dataclass(frozen=True)
class _PlatformRegistration:
    """One platform's factory, and the platform that holds what it built."""

    platform: EntityPlatform
    factory: EntityFactory


# What a platform builds for one module device: the entities, or nothing.
# The int is the Designer module row id that keys ``module_device_ids``.
type ModuleEntityFactory = Callable[[AmpioData, int], Iterable[Entity]]


@dataclass(frozen=True)
class _ModulePlatformRegistration:
    """One platform's module factory, and whether the account tier gates it."""

    platform: EntityPlatform
    factory: ModuleEntityFactory
    admin_only: bool = False


class AmpioData:
    """Runtime data for one Ampio server: the device tree the catalogue defines.

    ``async_create`` builds the hub, one module device per Designer module
    row, and the room map. ``ensure_module_device`` creates the module
    device of a row the tree meets later, through the same path, and
    ``forget_module_device`` drops one the user deleted.
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
        # The tier is fixed at client construction from the login name, so
        # it cannot change while an entry is loaded. Every site reads this
        # instead of testing whether an administrator-only field happens to
        # be populated.
        self.is_admin = client.access_tier is AccessTier.ADMIN
        # Registry ids the object child devices parent to: the hub, and one
        # module device per Designer module row.
        self.hub_device_id = hub_device_id
        self.module_device_ids: dict[int, str] = {}
        # The app room of each object, from the tier-shared room tables. It
        # seeds a child device's area once, at the device's first creation.
        self.rooms: dict[int, str] = {}
        # The Designer row of the M-SERV itself. Its objects sit on the hub.
        self.mserv_id = mserv_id
        self._platforms: list[_PlatformRegistration] = []
        self._module_platforms: list[_ModulePlatformRegistration] = []
        # One fingerprint per object in the catalogue, eligible or not, so
        # that a state push from any object costs one dictionary lookup.
        self._fingerprints: dict[int, Fingerprint] = {}
        # The object keys queued for the next batch, by object id.
        self._pending: dict[int, str] = {}
        self._reconcile_lock = asyncio.Lock()
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=RECONCILE_COOLDOWN,
            immediate=False,
            function=self._async_schedule_reconcile,
            background=True,
        )
        # The stale-record report, handed over once every platform loaded.
        # A batch that runs while a platform is still loading would report
        # every entity that platform has not built yet, so nothing reports
        # before setup says so.
        self._report: Callable[[], None] | None = None

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
        is_admin = client.access_tier is AccessTier.ADMIN
        # The module catalogue answers the administrator login alone, so the
        # read itself is gated rather than its result.
        mserv = client.mserv if is_admin else None
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
        # both tiers receive those. Reading the admin catalogue instead would
        # build one tree for an administrator and another for a standard
        # account wherever the two disagree, and a server-owned object
        # reaches the hub through ``is_server_owned`` without this value. A
        # split vote goes to the row most objects name, and ties to the first
        # one seen.
        server_rows = Counter(
            obj.id_urzadzenia for obj in eligible_objects(client) if obj.is_server_owned
        )
        mserv_id: int | None = None
        if server_rows:
            mserv_id = server_rows.most_common(1)[0][0]
        data = cls(hass, entry, client, hub.id, mserv_id)

        # One device per Designer module row, registered before the
        # platforms load. The first eligible object on a row stands for it;
        # every object carries the row id, so any of them will do.
        module_reps: dict[int, AmpioObject] = {}
        for obj in eligible_objects(client):
            module_id = obj.id_urzadzenia
            if obj.is_server_owned or module_id == mserv_id:
                continue
            module_reps.setdefault(module_id, obj)
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
    def module_row(self, module_id: int) -> AmpioModule | None:
        """The module catalogue row for a Designer row id, or None.

        Every read after the hub build goes through here. ``async_create``
        reads ``client.mserv`` on its own to build the hub, before this
        instance exists to read it through. The M-SERV serves the catalogue
        to the reserved admin login alone, and the library raises on a
        standard account rather than reading as an install with no modules,
        so the tier test belongs in one place for every read that can reach
        it.

        None covers two cases that need the same answer. The account is not
        served the catalogue, or the row left it mid-session: Ampio Designer
        deletes a device at once and only unassigns its objects, so a visible
        object can sit on a row the catalogue no longer carries.
        """
        if not self.is_admin:
            return None
        return self.client.modules.get(module_id)

    def _module_name(self, module_id: int) -> str:
        """The installer's name when the catalogue has one, else the row.

        ``nazwa_urzadzenia`` comes from the module catalogue, which answers
        the administrator login alone, so this name follows the account
        tier. Nothing depends on it: ``AmpioPinnedEntity`` pins the entity
        id, so a name that changes on a tier switch renames the device in
        the interface and moves no id.

        The lookup tolerates a missing row. Ampio Designer deletes a device
        at once and only unassigns its objects into a collapsed UNGROUPED
        section, so between those two steps a visible object sits on a row
        with no catalogue entry.
        """
        module = self.module_row(module_id)
        if module is not None and module.nazwa_urzadzenia:
            return module.nazwa_urzadzenia
        return f"Ampio module {module_id}"

    @callback
    def ensure_module_device(self, obj: AmpioObject) -> int | None:
        """Create the module device of the object's row, unless it exists.

        Returns the row id when this call put the row into the tree, which
        is when the module platforms owe the row their entities, and None
        when the row needs no device or already has one.

        The row id rides every object on both account tiers, so the tree
        holds still across a tier change. The admin catalogue names the
        module and decorates the model, the versions, and the serial; a
        standard account gets the row id in the name and no decoration.
        None of those reaches an entity id.
        """
        module_id = obj.id_urzadzenia
        if (
            obj.is_server_owned
            or module_id == self.mserv_id
            or module_id in self.module_device_ids
        ):
            return None
        module = self.module_row(module_id)
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={module_identifier(module_id)},
            name=self._module_name(module_id),
            manufacturer="Ampio",
            via_device_id=self.hub_device_id,
            model=module.model if module else None,
            sw_version=str(module.wersja_softu) if module else None,
            hw_version=str(module.wersja_pcb) if module else None,
            serial_number=str(module.mac_global) if module else None,
        )
        self.module_device_ids[module_id] = device.id
        return module_id

    @callback
    def forget_module_device(self, device_id: str) -> None:
        """Drop a module device from the tree, so that its row's return builds it again.

        The removal hook calls it when it permits the delete of a module
        device. The registry keeps the deleted record, so the next object
        on the row gets the device back through ``ensure_module_device``,
        with its id, its user name, and its area.
        """
        self.module_device_ids = {
            module_id: known
            for module_id, known in self.module_device_ids.items()
            if known != device_id
        }

    @callback
    def async_add_platform(
        self, factory: EntityFactory, async_add_entities: AddConfigEntryEntitiesCallback
    ) -> None:
        """Register a platform, and build its entities for the catalogue as it stands.

        Valid inside the platform's ``async_setup_entry`` alone, where Home
        Assistant sets the current platform.
        """
        registration = _PlatformRegistration(async_get_current_platform(), factory)
        self._platforms.append(registration)
        entities: list[Entity] = []
        for obj in eligible_objects(self.client):
            entities.extend(self._expected_entities(registration, obj).values())
        async_add_entities(entities)

    @callback
    def async_add_module_platform(
        self,
        factory: ModuleEntityFactory,
        async_add_entities: AddConfigEntryEntitiesCallback,
        *,
        admin_only: bool = False,
    ) -> None:
        """Register a module factory, and build its entities for every module device.

        Valid inside the platform's ``async_setup_entry`` alone, where Home
        Assistant sets the current platform. A module device the tree meets
        later is built through the platform recorded here.

        ``admin_only`` marks a factory whose entities reach a surface the
        M-SERV serves the administrator login alone. A standard account is
        not given them at all, because an entity that can never do its job
        is worse than no entity.
        """
        registration = _ModulePlatformRegistration(
            async_get_current_platform(), factory, admin_only
        )
        self._module_platforms.append(registration)
        entities: list[Entity] = []
        if self._serves(registration):
            for module_id in self.module_device_ids:
                entities.extend(factory(self, module_id))
        async_add_entities(entities)

    def _serves(self, registration: _ModulePlatformRegistration) -> bool:
        """Whether this account is served what the registration builds."""
        return not registration.admin_only or self.is_admin

    def withheld_unique_ids(self) -> set[str]:
        """The unique ids the administrator rule withholds from this account.

        Empty on the administrator login. On a standard account it is every
        id the gated factories would have built, which is what tells a
        withheld record apart from one Ampio Designer really dropped. The
        factories run here exactly as they would have, so the two can never
        disagree.

        The bare ids returned here are safe to match against an entity's
        unique id only because Home Assistant scopes a unique id per
        platform domain and config entry, and every module factory
        namespaces its keys on ``module_`` while every object entity
        namespaces on ``obj_``. A factory that dropped that prefix could
        collide with another domain's unique id and this method would not
        tell the two apart.
        """
        return {
            uid
            for registration in self._module_platforms
            if not self._serves(registration)
            for module_id in self.module_device_ids
            for entity in registration.factory(self, module_id)
            if (uid := entity.unique_id) is not None
        }

    def _expected_entities(
        self, registration: _PlatformRegistration, obj: AmpioObject | None
    ) -> dict[str, Entity]:
        """The entities a platform builds for an object, keyed by unique id.

        Nothing for an object that left the catalogue, for a hidden or
        system object, and for an object whose child device hangs under a
        parent it has outgrown. Home Assistant cannot re-parent a child, so
        the delete of that device is the move, and the repair offers it.
        """
        if obj is None or not obj.visible or obj.is_system:
            return {}
        entities = {
            uid: entity
            for entity in registration.factory(self, obj)
            if (uid := entity.unique_id) is not None
        }
        if entities and self._misparented(obj):
            _LOGGER.warning(
                "Object %s moved to another module in Ampio Designer, so its "
                "entities are removed. Use the repair on the Settings page, or "
                "delete its device, and it comes back under the new module",
                obj.id,
            )
            return {}
        return entities

    @callback
    def _misparented(self, obj: AmpioObject) -> bool:
        """Whether the object's child device hangs under a parent it has outgrown."""
        child = dr.async_get(self.hass).async_get_child_device_by_identifier(
            (DOMAIN, obj.object_key), self.entry.entry_id
        )
        return child is not None and child.parent_device_id != self.parent_for(obj)

    @callback
    def async_subscribe(self) -> Callable[[], None]:
        """Watch the catalogue, and return the callable that stops watching."""
        for obj in self.client.objects.values():
            self._fingerprints[obj.id] = fingerprint(obj)
        unsubscribe = self.client.subscribe(
            self._catalogue_event, of=(ObjectUpdated, ObjectRemoved)
        )

        @callback
        def _stop() -> None:
            unsubscribe()
            self._debouncer.async_shutdown()

        return _stop

    @callback
    def async_mark_ready(self, report: Callable[[], None]) -> None:
        """Let every batch from now on call ``report`` after it reconciles."""
        self._report = report

    @callback
    def _catalogue_event(self, event: ObjectUpdated | ObjectRemoved) -> None:
        """Queue an object whose catalogue row changed, and let a state push pass."""
        obj = event.object
        if isinstance(event, ObjectUpdated) and self._fingerprints.get(
            obj.id
        ) == fingerprint(obj):
            return
        self.async_request_reconcile(obj)

    @callback
    def async_request_reconcile(self, obj: AmpioObject) -> None:
        """Queue an object for the next batch, as a catalogue change would.

        The removal hook calls it for a moved object's child: the delete is
        the move, and the batch that follows builds the child again under
        the parent the object resolves to now.
        """
        self._pending[obj.id] = obj.object_key
        self._debouncer.async_schedule_call()

    @callback
    def _async_schedule_reconcile(self) -> None:
        """Run the batch as an entry task, so that an unload cancels it."""
        self.entry.async_create_background_task(
            self.hass, self._async_reconcile(), "ampio_reconcile"
        )

    async def _async_reconcile(self) -> None:
        """Bring every pending object's entities in line with the catalogue.

        One rule, expected versus built, covers a new object, a deletion, a
        hide, an un-hide, a re-tag, a pulse time, and a move.
        """
        async with self._reconcile_lock:
            pending, self._pending = self._pending, {}
            if not pending:
                return
            buildable: list[AmpioObject] = []
            for oid in pending:
                obj = self.client.objects.get(oid)
                if obj is None:
                    self._fingerprints.pop(oid, None)
                    continue
                self._fingerprints[oid] = fingerprint(obj)
                if obj.visible and not obj.is_system and not self._misparented(obj):
                    buildable.append(obj)
            # An entity reads the room map and the module device when it is
            # built, so both precede the factories.
            new_rows: list[int] = []
            if buildable:
                await self._async_refresh_rooms()
                new_rows.extend(
                    row
                    for obj in buildable
                    if (row := self.ensure_module_device(obj)) is not None
                )
            for registration in self._platforms:
                to_add: list[Entity] = []
                to_remove: list[Entity] = []
                for oid, object_key in pending.items():
                    built = _built_entities(registration.platform, object_key)
                    expected = self._expected_entities(
                        registration, self.client.objects.get(oid)
                    )
                    to_add.extend(
                        entity for uid, entity in expected.items() if uid not in built
                    )
                    to_remove.extend(
                        entity for uid, entity in built.items() if uid not in expected
                    )
                for entity in to_remove:
                    # The registry record stays, so the stale repair lists it
                    # and a re-add restores the id.
                    await entity.async_remove()
                if to_add:
                    # Awaited, so that the platform's table holds the entities
                    # before the batch ends.
                    await registration.platform.async_add_entities(to_add)
            # A module device the batch created owes the module platforms
            # their entities, awaited for the same reason as the objects.
            for module_registration in self._module_platforms:
                if not self._serves(module_registration):
                    continue
                module_entities = [
                    entity
                    for row in new_rows
                    for entity in module_registration.factory(self, row)
                ]
                if module_entities:
                    await module_registration.platform.async_add_entities(
                        module_entities
                    )
            if self._report is not None:
                self._report()

    async def _async_refresh_rooms(self) -> None:
        """Re-read the room map, so that a new child takes its app room."""
        try:
            self.rooms = await self.client.fetch_rooms()
        except AmpioConnectionError:
            _LOGGER.warning(
                "Could not fetch the Ampio room map; the new devices get no area suggestion"
            )

    def parent_for(self, obj: AmpioObject) -> str:
        """The device an object's child hangs under: its module, or the hub.

        The Designer module row id rides every object row on both account
        tiers, leaf or no leaf, so the tree never depends on the leaf id. The
        M-SERV's own objects sit on the hub.
        """
        module_id = obj.id_urzadzenia
        if obj.is_server_owned or module_id == self.mserv_id:
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
            if parent != self.hub_device_id:
                live.add(module_identifier(obj.id_urzadzenia))
            live.add((DOMAIN, obj.object_key))
            expected_parent[(DOMAIN, obj.object_key)] = parent
        return live, expected_parent


type AmpioConfigEntry = ConfigEntry[AmpioData]
