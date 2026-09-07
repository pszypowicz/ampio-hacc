"""Runtime data for the Ampio integration: the device tree the catalogue defines."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from ampio_mqtt import AmpioClient, AmpioObject

from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN

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


@dataclass
class AmpioData:
    """Runtime data for one Ampio server."""

    client: AmpioClient
    # Registry ids the object child devices parent to: the hub, and one
    # module device per Designer module row. Setup fills them before the
    # platforms load.
    hub_device_id: str
    module_device_ids: dict[int, str]
    # The app room of each object, from the tier-shared room tables. It
    # seeds a child device's area once, at the device's first creation.
    rooms: dict[int, str]
    # The Designer row of the M-SERV itself. Its objects sit on the hub.
    mserv_id: int | None

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
