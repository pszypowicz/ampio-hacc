"""Ampio MQTT api implementation."""
import asyncio
from itertools import groupby
import logging
from operator import attrgetter
import re
from typing import Any, List, Optional, Union

import voluptuous as vol

from homeassistant.components.mqtt.subscription import EntitySubscription as Subscription
from homeassistant.components.mqtt.models import MessageCallbackType
from homeassistant.const import CONF_CLIENT_ID, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import Callable, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import dispatcher_send
from homeassistant.helpers.typing import ConfigType, HomeAssistantType
from homeassistant.loader import bind_hass
from homeassistant.util import dt as dt_util
from homeassistant.util.logging import catch_log_exception

from . import discovery
from .const import (
    AMPIO_CONNECTED,
    AMPIO_DISCONNECTED,
    CONF_BROKER,
    DATA_AMPIO,
    DATA_AMPIO_API,
    DATA_AMPIO_CONFIG,
    DEFAULT_QOS,
    DOMAIN,
    PROTOCOL_311,
)
from .models import Message, PublishPayloadType

# ampio/from/1B88/description
MAC_FROM_TOPIC_RE = re.compile(r"^ampio/from/(?P<mac>.*)/.*$")

AMPIO_EMPTY_PAYLOAD = ""


CONF_KEEPALIVE = "keepalive"

PROTOCOL_31 = "3.1"

DEFAULT_PORT = 1883
DEFAULT_KEEPALIVE = 60
DEFAULT_PROTOCOL = PROTOCOL_311
DEFAULT_PREFIX = "ampio"

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
                }
            )
        )
    }
)

SubscribePayloadType = Union[str, bytes]  # Only bytes if encoding is None


_LOGGER = logging.getLogger(__name__)


async def async_setup_discovery(
    hass: HomeAssistantType, conf: ConfigType, config_entry
) -> bool:
    """Start Ampio Discovery."""
    success: bool = await discovery.async_start(hass, config_entry)

    return success


@callback
@bind_hass
def async_publish(
    hass: HomeAssistantType, topic: Any, payload, qos=None, retain=None
) -> None:
    """Publish message to an MQTT topic."""
    hass.add_job(
        hass.data[DATA_AMPIO][DATA_AMPIO_API].async_publish, topic, payload, qos, retain
    )


@bind_hass
async def async_subscribe(
    hass: HomeAssistantType,
    topic: str,
    msg_callback: MessageCallbackType,
    qos: int = DEFAULT_QOS,
    encoding: Optional[str] = "utf-8",
):
    """Subscribe to an MQTT topic.
    Call the return value to unsubscribe.
    """
    async_remove = await hass.data[DATA_AMPIO][DATA_AMPIO_API].async_subscribe(
        topic,
        catch_log_exception(
            msg_callback,
            lambda msg: (
                f"Exception in {msg_callback.__name__} when handling msg on "
                f"'{msg.topic}': '{msg.payload}'"
            ),
        ),
        qos,
        encoding,
    )
    return async_remove


class AmpioAPI:
    """Ampio MQTT API."""

    def __init__(self, hass: HomeAssistantType, config_entry, conf,) -> None:
        """Initialize Ampio Client."""
        import paho.mqtt.client as mqtt  # pylint: disable=import-outside-toplevel

        self.hass = hass
        self.config_entry = config_entry
        self.conf = conf
        self.subscriptions: List[Subscription] = []
        self.connected = False
        self._mqttc: mqtt.Client = None
        self._paho_lock = asyncio.Lock()

        self.init_client()
        self.config_entry.add_update_listener(self.async_config_entry_updated)

    @staticmethod
    async def async_config_entry_updated(hass, entry) -> None:
        """Handle signals of config entry being updated.

        This is a static method because a class method (bound method), can not be used with weak references.
        Causes for this is config entry options changing.
        """
        self = hass.data[DATA_AMPIO][DATA_AMPIO_API]
        conf = hass.data.get(DATA_AMPIO_CONFIG)
        if conf is None:
            conf = CONFIG_SCHEMA({DOMAIN: dict(entry.data)})[DOMAIN]

        self.conf = _merge_config(entry, conf)
        await self.async_disconnect()
        self.init_client()
        await self.async_connect()

        await discovery.async_stop(hass)
        await async_setup_discovery(hass, self.conf, entry)

    def init_client(self):
        """Initialize paho client."""
        # We don't import on the top because some integrations
        # should be able to optionally rely on MQTT.
        import paho.mqtt.client as mqtt  # pylint: disable=import-outside-toplevel

        client_id = self.conf.get(CONF_CLIENT_ID)
        if client_id is None:
            self._mqttc = mqtt.Client(protocol=mqtt.MQTTv311)
        else:
            self._mqttc = mqtt.Client(client_id, protocol=mqtt.MQTTv311)

        username = self.conf.get(CONF_USERNAME)
        password = self.conf.get(CONF_PASSWORD)
        if username is not None:
            self._mqttc.username_pw_set(username, password)

        self._mqttc.on_connect = self._mqtt_on_connect
        self._mqttc.on_disconnect = self._mqtt_on_disconnect
        self._mqttc.on_message = self._mqtt_on_message

    async def async_publish(
        self, topic: str, payload: PublishPayloadType, qos: int, retain: bool
    ) -> None:
        """Publish MQTT message."""
        async with self._paho_lock:
            _LOGGER.debug("Transmitting message on %s: %s", topic, payload)
            await self.hass.async_add_executor_job(
                self._mqttc.publish, topic, payload, qos, retain
            )

    async def async_connect(self) -> str:
        """Connect to the host. Does not process messages yet."""
        # pylint: disable=import-outside-toplevel
        import paho.mqtt.client as mqtt

        result: int = None
        try:
            result = await self.hass.async_add_executor_job(
                self._mqttc.connect,
                self.conf[CONF_BROKER],
                self.conf[CONF_PORT],
                self.conf[CONF_KEEPALIVE],
            )
        except OSError as err:
            _LOGGER.error(
                "Failed to connect to Ampio MQTT Server due to exceptipn: %s", err
            )

        if result is not None and result != 0:
            _LOGGER.error(
                "Failed to connect to Ampio MQTT Server: %s", mqtt.error_string(result)
            )

        self._mqttc.loop_start()

    async def async_disconnect(self):
        """Stop MQTT Client."""

        def stop():
            """Stop the MQTT Client."""
            # Do not disconnect, we want the broker to always publish will
            self._mqttc.loop_stop()

        await self.hass.async_add_executor_job(stop)

    async def async_subscribe(
        self,
        topic: str,
        msg_callback: MessageCallbackType,
        qos: int,
        encoding: Optional[str] = None,
    ) -> Callable[[], None]:
        """Set up a subscription to a topic with the provided qos.
        This method is a coroutine.
        """
        if not isinstance(topic, str):
            raise HomeAssistantError("Topic needs to be a string!")

        subscription = Subscription(topic, lambda: True, msg_callback, qos, encoding)
        self.subscriptions.append(subscription)

        # Only subscribe if currently connected.
        if self.connected:
            await self._async_perform_subscription(topic, qos)

        @callback
        def async_remove() -> None:
            """Remove subscription."""
            if subscription not in self.subscriptions:
                raise HomeAssistantError("Can't remove subscription twice")
            self.subscriptions.remove(subscription)

            if any(other.topic == topic for other in self.subscriptions):
                # Other subscriptions on topic remaining - don't unsubscribe.
                return

            # Only unsubscribe if currently connected.
            if self.connected:
                self.hass.async_create_task(self._async_unsubscribe(topic))

        return async_remove

    async def _async_unsubscribe(self, topic: str) -> None:
        """Unsubscribe from a topic.
        This method is a coroutine.
        """
        _LOGGER.debug("Unsubscribing from %s", topic)
        async with self._paho_lock:
            result: int = None
            result, _ = await self.hass.async_add_executor_job(
                self._mqttc.unsubscribe, topic
            )
            _raise_on_error(result)

    async def _async_perform_subscription(self, topic: str, qos: int) -> None:
        """Perform a paho-mqtt subscription."""
        _LOGGER.debug("Subscribing to %s", topic)

        async with self._paho_lock:
            result: int = None
            result, _ = await self.hass.async_add_executor_job(
                self._mqttc.subscribe, topic, qos
            )
            _raise_on_error(result)

    def _mqtt_on_connect(self, _mqttc, _userdata, _flags, result_code: int) -> None:
        """On connect callback.
        Resubscribe to all topics we were subscribed to and publish birth
        message.
        """
        # pylint: disable=import-outside-toplevel
        import paho.mqtt.client as mqtt

        if result_code != mqtt.CONNACK_ACCEPTED:
            _LOGGER.error(
                "Unable to connect to the MQTT broker: %s",
                mqtt.connack_string(result_code),
            )
            return

        self.connected = True
        dispatcher_send(self.hass, AMPIO_CONNECTED)
        _LOGGER.info(
            "Connected to Ampio MQTT Server %s:%s (%s)",
            self.conf[CONF_BROKER],
            self.conf[CONF_PORT],
            result_code,
        )

        # Group subscriptions to only re-subscribe once for each topic.
        keyfunc = attrgetter("topic")
        for topic, subs in groupby(sorted(self.subscriptions, key=keyfunc), keyfunc):
            # Re-subscribe with the highest requested qos
            max_qos = max(subscription.qos for subscription in subs)
            self.hass.add_job(self._async_perform_subscription, topic, max_qos)

    def _mqtt_on_message(self, _mqttc, _userdata, msg) -> None:
        """Message received callback."""
        self.hass.add_job(self._mqtt_handle_message, msg)

    @callback
    def _mqtt_handle_message(self, msg) -> None:
        _LOGGER.debug(
            "Received message on %s%s: %s",
            msg.topic,
            " (retained)" if msg.retain else "",
            msg.payload,
        )
        timestamp = dt_util.utcnow()

        for subscription in self.subscriptions:
            if not _match_topic(subscription.topic, msg.topic):
                continue

            payload: SubscribePayloadType = msg.payload
            if subscription.encoding is not None:
                try:
                    payload = msg.payload.decode(subscription.encoding)
                except (AttributeError, UnicodeDecodeError):
                    _LOGGER.warning(
                        "Can't decode payload %s on %s with encoding %s (for %s)",
                        msg.payload,
                        msg.topic,
                        subscription.encoding,
                        subscription.job,
                    )
                    continue

            self.hass.async_run_job(
                subscription.job,
                Message(
                    msg.topic,
                    payload,
                    msg.qos,
                    msg.retain,
                    subscription.topic,
                    timestamp,
                ),
            )

    def _mqtt_on_disconnect(self, _mqttc, _userdata, result_code: int) -> None:
        """Disconnected callback."""
        self.connected = False
        dispatcher_send(self.hass, AMPIO_DISCONNECTED)
        _LOGGER.warning(
            "Disconnected from MQTT server %s:%s (%s)",
            self.conf[CONF_BROKER],
            self.conf[CONF_PORT],
            result_code,
        )


def _raise_on_error(result_code: int) -> None:
    """Raise error if error result."""
    # pylint: disable=import-outside-toplevel
    import paho.mqtt.client as mqtt

    if result_code != 0:
        raise HomeAssistantError(
            f"Error talking to Ampio MQTT: {mqtt.error_string(result_code)}"
        )


def _match_topic(subscription: str, topic: str) -> bool:
    """Test if topic matches subscription."""
    # pylint: disable=import-outside-toplevel
    from paho.mqtt.matcher import MQTTMatcher

    matcher = MQTTMatcher()
    matcher[subscription] = True
    try:
        next(matcher.iter_match(topic))
        return True
    except StopIteration:
        return False


def _merge_config(entry, conf):
    """Merge configuration.yaml config with config entry."""
    return {**conf, **entry.data}
