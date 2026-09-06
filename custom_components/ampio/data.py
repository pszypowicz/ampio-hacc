"""Runtime data for the Ampio integration."""

from dataclasses import dataclass

from ampio_mqtt import AmpioClient

from homeassistant.config_entries import ConfigEntry


@dataclass
class AmpioData:
    """Runtime data for one Ampio server."""

    client: AmpioClient
    # Registry ids the object child devices parent to: the hub, and one
    # module device per leaf mac. Setup fills them before the platforms load.
    hub_device_id: str
    module_device_ids: dict[int, str]
    # The app room of each object, from the tier-shared room tables. It
    # seeds a child device's area once, at the device's first creation.
    rooms: dict[int, str]
    # The parent each object's child device already sits under, read from
    # the registry at setup. A child device cannot be re-parented, so this
    # is the parent an entity must keep naming; an object missing here has
    # no child device yet and takes the parent it resolves to.
    child_parent_ids: dict[str, str]


type AmpioConfigEntry = ConfigEntry[AmpioData]
