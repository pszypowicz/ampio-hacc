"""Base entity for the Ampio integration."""

import asyncio
from typing import override

from ampio_mqtt import (
    AmpioClient,
    AmpioObject,
    AvailabilityChanged,
    ObjectRemoved,
    ObjectUpdated,
)

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import ChildDeviceInfo, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import EntityPlatform

from .const import DOMAIN
from .data import AmpioData, module_identifier


async def async_turn_on_honoring_pulse(
    client: AmpioClient, obj: AmpioObject | None, object_id: int
) -> None:
    """Send the on write, timed when Designer configures a pulse length.

    The M-SERV never applies the configured time server-side; the app
    reads the column and sends the timed command itself, and so does the
    integration. Without a configured time (or once the object is gone),
    the plain verb latches, which is the server truth either way.
    """
    if obj is not None and obj.pulse_ms:
        await client.set_value(object_id, 255, pulse_ms=obj.pulse_ms)
    else:
        await client.turn_on(object_id)


class AmpioPinnedEntity(Entity):
    """Entity whose id is pinned to its unique id, so that no name composes it."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    # The unique id, and the object part of the pinned entity id. A subclass
    # sets it before the add, and sets ``_attr_unique_id`` to the same string.
    _key: str

    @override
    def add_to_platform_start(
        self,
        hass: HomeAssistant,
        platform: EntityPlatform,
        parallel_updates: asyncio.Semaphore | None,
    ) -> None:
        """Pin the entity id, so that no name composes one.

        Home Assistant builds an entity id from the area name, the device
        name, and the entity name, once, at first registration. An entity
        that carries an ``entity_id`` into the add is exempt: the platform
        stores the object part as the registry's ``suggested_object_id``,
        and the composition then skips every name part. The module device
        is therefore free to take its administrator-tier name, which the
        restricted tier is not served, without moving an id.

        The pinned id is the unique id with the domain in front, so the two
        identities are one string and cannot drift apart.
        """
        super().add_to_platform_start(hass, platform, parallel_updates)
        self.entity_id = f"{platform.domain}.ampio_{self._key}"


class AmpioEntity(AmpioPinnedEntity):
    """Entity backed by one Ampio object."""

    def __init__(
        self, data: AmpioData, obj: AmpioObject, *, key_suffix: str = ""
    ) -> None:
        """Initialize from the discovery-time object snapshot.

        ``key_suffix`` separates a second entity built from one object, and
        it lands in the unique id and the entity id alike, because the two
        are the same string. No server scope: object ids are unique per
        M-SERV, and one M-SERV is allowed.
        """
        self._data = data
        self._object_id = obj.id
        # Designer exposes one physical output as several objects, and every
        # such view repeats the ``leaf_id`` that ``leaf_key`` is built
        # from. ``object_key`` identifies the row instead, so each view
        # keeps its own entity.
        self._key = f"{obj.object_key}{key_suffix}"
        self._attr_unique_id = self._key
        # An object is a channel of its module, and its child device hangs
        # under that module. The registry cannot re-parent a child, so a
        # move in Designer needs a delete, which the removal hook permits.
        parent = data.parent_for(obj)
        device_info = ChildDeviceInfo(
            identifiers={(DOMAIN, obj.object_key)}, parent_device_id=parent
        )
        # ``opis_menu`` is the Designer menu description, which is the name
        # the user gave the object in the Ampio app. The device carries it,
        # so the primary entity adds no name of its own. An unnamed object
        # reads a translated placeholder, and its entity keeps the
        # platform's kind name.
        if obj.opis_menu:
            device_info["name"] = obj.opis_menu
            self._attr_name = None
        else:
            device_info["translation_key"] = "object"
            device_info["translation_placeholders"] = {"id": str(obj.id)}
        # The app room seeds the area once, at the device's first creation.
        # The registry never moves a device on a later suggestion.
        if (room := data.rooms.get(obj.id)) is not None:
            device_info["suggested_area"] = room
        self._attr_device_info = device_info

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to the pushes that affect this entity's state."""
        client = self._data.client
        self.async_on_remove(
            client.subscribe(
                self._push_received,
                of=(ObjectUpdated, ObjectRemoved),
                object_id=self._object_id,
            )
        )
        self.async_on_remove(
            client.subscribe(self._push_received, of=AvailabilityChanged)
        )

    @callback
    def _push_received(
        self, event: ObjectUpdated | ObjectRemoved | AvailabilityChanged
    ) -> None:
        """Write state when the backing object or the connection changes."""
        self.async_write_ha_state()

    @property
    def _object(self) -> AmpioObject | None:
        """The backing object, or None once the catalogue dropped it."""
        return self._data.client.objects.get(self._object_id)

    @property
    @override
    def available(self) -> bool:
        """Available while the broker is connected and the object is still shown.

        A Designer delete keeps the row on the administrator tier and sets
        its hidden bit, so ``visible`` is the delete signal there. The
        restricted tier drops the row instead, and ``_object`` goes None.
        """
        obj = self._object
        return self._data.client.available and obj is not None and obj.visible


class AmpioModuleEntity(AmpioPinnedEntity):
    """Entity that attaches to a module device rather than to an object.

    The key is the Designer row id. An object carries that same id in its
    own ``id_urzadzenia`` field on both account tiers, which is how the row
    id survives a tier change. Availability tracks the connection and nothing
    else. A subclass whose surface reports something of its own overrides
    ``available``.
    """

    def __init__(self, data: AmpioData, module_id: int, *, key_suffix: str) -> None:
        """Attach to the module device of Designer row ``module_id``.

        ``key_suffix`` names what the entity does on the module, and it
        lands in the unique id and the entity id alike, because the two are
        the same string.
        """
        self._data = data
        self._module_id = module_id
        self._key = f"module_{module_id}_{key_suffix}"
        self._attr_unique_id = self._key
        self._attr_device_info = DeviceInfo(identifiers={module_identifier(module_id)})

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the connection, which is what availability reads."""
        self.async_on_remove(
            self._data.client.subscribe(
                self._connection_changed, of=AvailabilityChanged
            )
        )

    @callback
    def _connection_changed(self, event: AvailabilityChanged) -> None:
        """Write state when the connection comes up or goes down."""
        self.async_write_ha_state()

    @property
    @override
    def available(self) -> bool:
        """Available while the broker is connected."""
        return self._data.client.available
