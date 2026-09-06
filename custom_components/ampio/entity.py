"""Base entity for the Ampio integration."""

import asyncio
from collections.abc import Iterator, Mapping
from typing import Final, override

from ampio_mqtt import (
    AmpioClient,
    AmpioObject,
    AvailabilityChanged,
    ObjectRemoved,
    ObjectUpdated,
)

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import ChildDeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import EntityPlatform

from .const import DOMAIN
from .data import AmpioData


def eligible_objects(client: AmpioClient) -> Iterator[AmpioObject]:
    """The objects any platform may expose as entities.

    ``visible`` is the M-SERV's own predicate for what the user still sees
    in Ampio Designer: the hidden bit alone. A row without a ``leaf_id`` is
    still an object, because Designer clears that field when an object's
    Matter box is unchecked. ``is_system`` then holds back the M-SERV's
    own detection and simulation objects, which no platform covers.
    """
    return (obj for obj in client.objects.values() if obj.visible and not obj.is_system)


# The registry identifiers carry no server mac. Object ids live in the
# Designer database, which moves to new hardware with the project; the
# server mac does not. One M-SERV per Home Assistant keeps them unique.
HUB_IDENTIFIER: Final = (DOMAIN, "hub")


def module_identifier(mac: int) -> tuple[str, str]:
    """The registry identifier of the module device that carries ``mac``."""
    return (DOMAIN, f"module:{mac}")


def resolve_parent(
    obj: AmpioObject, hub_device_id: str, module_device_ids: Mapping[int, str]
) -> str:
    """The device an object's child device belongs under.

    An object is a channel of the module that carries it, so its child
    hangs under that module. The mac comes from the leaf id, or from the
    leaf-bearing siblings on the same module when Designer cleared the
    leaf; both ride the object catalogue every account tier receives. The
    hub takes a server-owned object, an object with neither mac, and an
    object whose mac names no module device the account was served.
    """
    if obj.is_server_owned:
        return hub_device_id
    mac = obj.module_mac or obj.sibling_module_mac
    if mac is None:
        return hub_device_id
    return module_device_ids.get(mac, hub_device_id)


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


class AmpioEntity(Entity):
    """Entity backed by one Ampio object."""

    _attr_has_entity_name = True
    _attr_should_poll = False

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
        # The registry cannot re-parent a child device, and it rejects the
        # entity outright when the device info names another parent than
        # the one the child was created under. So the stored parent wins
        # over the one the object resolves to now, which keeps the entity
        # in the set; setup reports the mismatch, and a delete repairs it.
        parent = data.child_parent_ids.get(obj.object_key) or resolve_parent(
            obj, data.hub_device_id, data.module_device_ids
        )
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
        """Available while the broker is connected and the object exists."""
        return self._data.client.available and self._object is not None
