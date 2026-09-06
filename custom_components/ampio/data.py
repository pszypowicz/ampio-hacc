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


type AmpioConfigEntry = ConfigEntry[AmpioData]
