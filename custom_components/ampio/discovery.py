"""Module and entity discovery."""
import asyncio
import json
import logging
import re
from typing import Any, Callable, Dict, List

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
import homeassistant.helpers.device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import HomeAssistantType

from . import client as ampio, subscription
from .const import (
    ATTR_VERSION,
    CONFIG_ENTRY_IS_SETUP,
    DATA_AMPIO,
    DATA_AMPIO_MODULES,
    DATA_AMPIO_PLATFORM_LOADED,
    DATA_AMPIO_UNIQUE_IDS,
    DATA_CONFIG_ENTRY_LOCK,
    DEFAULT_QOS,
    DOMAIN,
    SIGNAL_ADD_ENTITIES,
)
from .models import AmpioModuleInfo, ItemName

REQUEST_AMPIO_VERSION = "ampio/to/info/version"
RESPONSE_AMPIO_VERSION = "ampio/from/info/version"

REQUEST_MODULE_DISCOVERY = "ampio/to/can/dev/list"
RESPONSE_MODULE_DISCOVERY = "ampio/from/can/dev/list"

REQUEST_MODULE_NAMES = "ampio/to/{mac}/description"
RESPONSE_MODULE_NAMES = "ampio/from/+/description"

DISCOVERY_UNSUBSCRIBE = "ampio_discovery_unsubscribe"

# ampio/from/1B88/description
MAC_FROM_TOPIC_RE = re.compile(r"^ampio/from/(?P<mac>.*)/.*$")

_LOGGER = logging.getLogger(__name__)


async def async_start(hass: HomeAssistantType, config_entry=None) -> bool:
    """Start Ampio discovery."""
    topics = {}

    @callback
    async def version_info_received(msg):
        """Process the version info message."""
        _LOGGER.debug("Version %s", msg.payload)
        try:
            data = json.loads(msg.payload)
        except json.JSONDecodeError:
            _LOGGER.error("Unable to decode Ampio MQTT Server version")
            return
        version = data.get(ATTR_VERSION, "N/A")
        device_registry = await hass.helpers.device_registry.async_get()
        device_registry.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            connections={(CONNECTION_NETWORK_MAC, str("ampio-mqtt"))},
            identifiers={(DOMAIN, str("ampio-mqtt"))},
            name="Ampio MQTT Server",
            manufacturer="Ampio",
            model="MQTT Server",
            sw_version=version,
        )

    topics[RESPONSE_AMPIO_VERSION] = {
        "topic": RESPONSE_AMPIO_VERSION,
        "msg_callback": version_info_received,
        "qos": DEFAULT_QOS,
    }

    @callback
    async def device_list_received(msg):
        """Process device list info message."""
        try:
            payload = json.loads(msg.payload)
        except ValueError as err:
            _LOGGER.error("Unable to parse JSON module list: %s", err)
            return

        modules: List[AmpioModuleInfo] = AmpioModuleInfo.from_topic_payload(payload)

        for module in modules:
            data_modules = hass.data[DATA_AMPIO_MODULES]
            await async_setup_device_registry(hass, config_entry, module)
            data_modules[module.user_mac] = module
            ampio.async_publish(
                hass, REQUEST_MODULE_NAMES.format(mac=module.user_mac), "1", 0, False
            )

    topics[RESPONSE_MODULE_DISCOVERY] = {
        "topic": RESPONSE_MODULE_DISCOVERY,
        "msg_callback": device_list_received,
        "qos": DEFAULT_QOS,
    }

    async def module_names_received(msg):
        "Handle names update." ""
        matched = MAC_FROM_TOPIC_RE.match(msg.topic)
        if matched:
            mac = matched.group("mac").upper()
            module = hass.data[DATA_AMPIO_MODULES].get(mac)
            if module is None:
                return
        else:
            return

        try:
            payload = json.loads(msg.payload)
        except ValueError as err:
            _LOGGER.error("Unable to parse JSON module names: %s", err)
            return

        module.names = ItemName.from_topic_payload(payload)
        module.update_configs()

        _LOGGER.info(
            "Discovered: %s-%s (%s): %s",
            module.code,
            module.model,
            module.software,
            module.name,
        )
        for component, configs in module.configs.items():
            for config in configs:
                unique_id = config.get("unique_id")
                if unique_id not in hass.data[DATA_AMPIO_UNIQUE_IDS]:
                    hass.data[DATA_AMPIO][component].append(config)
                    hass.data[DATA_AMPIO_UNIQUE_IDS].add(unique_id)
                else:
                    _LOGGER.debug("Ignoring: %s", unique_id)

        del hass.data[DATA_AMPIO_MODULES][mac]
        if len(hass.data[DATA_AMPIO_MODULES]) == 0:  # ALL MODULES discovered
            _LOGGER.info("All modules discovered")
            asyncio.create_task(async_load_entities(hass))

    topics[RESPONSE_MODULE_NAMES] = {
        "topic": RESPONSE_MODULE_NAMES,
        "msg_callback": module_names_received,
        "qos": DEFAULT_QOS,
    }

    hass.data[DATA_CONFIG_ENTRY_LOCK] = asyncio.Lock()
    hass.data[CONFIG_ENTRY_IS_SETUP] = set()
    hass.data[DATA_AMPIO_MODULES] = {}
    hass.data[DATA_AMPIO_UNIQUE_IDS] = set()

    hass.data[DISCOVERY_UNSUBSCRIBE] = await subscription.async_subscribe_topics(
        hass, hass.data.get(DISCOVERY_UNSUBSCRIBE), topics
    )

    ampio.async_publish(hass, REQUEST_AMPIO_VERSION, "", 0, False)
    ampio.async_publish(hass, REQUEST_MODULE_DISCOVERY, "1", 0, False)
    return True


@callback
async def async_stop(hass: HomeAssistantType) -> bool:
    """Stop Ampio MQTT Discovery."""
    hass.data[DISCOVERY_UNSUBSCRIBE] = await subscription.async_unsubscribe_topics(
        hass, hass.data[DISCOVERY_UNSUBSCRIBE]
    )


@callback
async def async_setup_device_registry(
    hass: HomeAssistantType, entry: ConfigEntry, device_info: AmpioModuleInfo
):
    """Set up device registry feature for a particular config entry."""
    device_registry = await dr.async_get(hass)
    return device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, **device_info.as_hass_device()
    )


@callback
async def async_add_entities(
    _async_add_entities: Callable, entities: List[Dict[str, Any]], klass
) -> None:
    """Add entities helper."""
    if not entities:
        return

    to_add = [klass(config) for config in entities]
    _async_add_entities(to_add, update_before_add=True)
    entities.clear()


async def async_load_entities(hass: HomeAssistantType) -> None:
    """Load entities after integration was setup."""
    to_setup = hass.data[DATA_AMPIO][DATA_AMPIO_PLATFORM_LOADED]
    results = await asyncio.gather(*to_setup, return_exceptions=True)
    for res in results:
        if isinstance(res, Exception):
            _LOGGER.warning("Couldn't setup Ampio platform: %s", res)
    async_dispatcher_send(hass, SIGNAL_ADD_ENTITIES)
