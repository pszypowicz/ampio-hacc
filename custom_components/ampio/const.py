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

# Registry records the last setup left unclaimed, and could not explain.
STALE_RECORDS_ISSUE: Final = "stale_records"
# Entity records the administrator rule withholds from a standard account.
# Separate, because the integration knows exactly why these went.
ADMIN_ONLY_RECORDS_ISSUE: Final = "admin_only_records"
