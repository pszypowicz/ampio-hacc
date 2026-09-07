"""Constants for the Ampio integration."""

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "ampio"

PLATFORMS: Final = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.LIGHT,
    Platform.SCENE,
    Platform.SENSOR,
    Platform.SWITCH,
]

DEFAULT_HOST: Final = "ampio.local"

# The one repair issue: registry records the last setup left unclaimed.
STALE_RECORDS_ISSUE: Final = "stale_records"
