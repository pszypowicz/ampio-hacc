"""Runtime data for the Ampio integration."""

from dataclasses import dataclass

from ampio_mqtt import AmpioClient

from homeassistant.config_entries import ConfigEntry


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


type AmpioConfigEntry = ConfigEntry[AmpioData]
