"""Runtime data for the Ampio integration."""

from dataclasses import dataclass

from ampio_mqtt import AmpioClient

from homeassistant.config_entries import ConfigEntry


@dataclass
class AmpioData:
    """Runtime data for one Ampio server."""

    client: AmpioClient


type AmpioConfigEntry = ConfigEntry[AmpioData]
