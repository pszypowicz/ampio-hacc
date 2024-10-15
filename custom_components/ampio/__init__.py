"""Ampio Systems Platform."""
import asyncio
import json
import logging
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_CLIENT_ID,
    CONF_DEVICE,
    CONF_DEVICE_CLASS,
    CONF_FRIENDLY_NAME,
    CONF_ICON,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_PROTOCOL,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import Event, callback
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    event,
    template,
)
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_registry import EntityRegistry, async_get
from homeassistant.helpers.typing import ConfigType, HomeAssistantType

from .client import AmpioAPI, async_setup_discovery
from .const import (
    AMPIO_CONNECTED,
    AMPIO_DISCOVERY_UPDATED,
    AMPIO_MODULE_DISCOVERY_UPDATED,
    COMPONENTS,
    CONF_BROKER,
    CONF_STATE_TOPIC,
    CONF_UNIQUE_ID,
    DATA_AMPIO,
    DATA_AMPIO_API,
    DATA_AMPIO_DISPATCHERS,
    DATA_AMPIO_PLATFORM_LOADED,
    PROTOCOL_311,
    SIGNAL_ADD_ENTITIES,
)
from .models import AmpioModuleInfo

_LOGGER = logging.getLogger(__name__)

DOMAIN = "ampio"


VERSION_TOPIC_FROM = "ampio/from/info/version"
VERSION_TOPIC_TO = "ampio/to/info/version"

DISCOVERY_TOPIC_FROM = "ampio/from/can/dev/list"
DISCOVERY_TOPIC_TO = "ampio/to/can/dev/list"

ATTR_DEVICES = "devices"

CONF_KEEPALIVE = "keepalive"

PROTOCOL_31 = "3.1"

DEFAULT_PORT = 1883
DEFAULT_KEEPALIVE = 60
DEFAULT_PROTOCOL = PROTOCOL_311


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.All(
            vol.Schema(
                {
                    vol.Optional(CONF_CLIENT_ID): cv.string,
                    vol.Optional(CONF_KEEPALIVE, default=DEFAULT_KEEPALIVE): vol.All(
                        vol.Coerce(int), vol.Range(min=15)
                    ),
                    vol.Optional(CONF_BROKER): cv.string,
                    vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
                    vol.Optional(CONF_USERNAME): cv.string,
                    vol.Optional(CONF_PASSWORD): cv.string,
                    vol.Optional(CONF_PROTOCOL, default=DEFAULT_PROTOCOL): vol.All(
                        cv.string, vol.In([PROTOCOL_31, PROTOCOL_311])
                    ),
                },
            ),
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistantType, config: ConfigType):
    """Stub to allow setting up this component.
    Configuration through YAML is not supported at this time.
    """
    return True


async def async_setup_entry(hass: HomeAssistantType, config_entry: ConfigEntry) -> bool:
    """Set up the Ampio component."""

    ampio_data = hass.data.setdefault(DATA_AMPIO, {})

    for component in COMPONENTS:
        ampio_data.setdefault(component, [])

    conf = CONFIG_SCHEMA({DOMAIN: dict(config_entry.data)})[DOMAIN]

    ampio_data[DATA_AMPIO_API]: AmpioAPI = AmpioAPI(
        hass, config_entry, conf,
    )

    ampio_data[DATA_AMPIO_DISPATCHERS] = []
    ampio_data[DATA_AMPIO_PLATFORM_LOADED] = []

    for component in COMPONENTS:
        coro = hass.config_entries.async_forward_entry_setup(config_entry, component)
        ampio_data[DATA_AMPIO_PLATFORM_LOADED].append(hass.async_create_task(coro))

    await ampio_data[DATA_AMPIO_API].async_connect()

    async def async_connected():
        """Start discovery on connected."""
        await async_setup_discovery(hass, conf, config_entry)

    async_dispatcher_connect(hass, AMPIO_CONNECTED, async_connected)

    async def async_stop_ampio(_event: Event):
        """Stop MQTT component."""
        await ampio_data[DATA_AMPIO_API].async_disconnect()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_stop_ampio)

    return True


async def async_unload_entry(hass, config_entry):
    """Unload ZHA config entry."""
    dispatchers = hass.data[DATA_AMPIO].get(DATA_AMPIO_DISPATCHERS, [])
    for unsub_dispatcher in dispatchers:
        unsub_dispatcher()

    for component in COMPONENTS:
        await hass.config_entries.async_forward_entry_unload(config_entry, component)

    return True
