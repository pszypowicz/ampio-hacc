"""Button platform for the Ampio integration."""

from collections.abc import Callable
from datetime import datetime
import logging
from typing import Final, override

from ampio_mqtt import (
    AccessTier,
    AmpioConnectionError,
    AmpioObject,
    AmpioTimeoutError,
    AvailabilityChanged,
)

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .data import AmpioConfigEntry, AmpioData, module_identifier
from .entity import AmpioEntity, AmpioPinnedEntity, async_turn_on_honoring_pulse

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# How long a module holds identify after a press. Ampio Designer's own
# button stops after the same 30 s, so the LED behaves as installers know it.
IDENTIFY_HOLD_SECONDS: Final = 30


def is_button(obj: AmpioObject) -> bool:
    """Whether the object belongs to the button platform.

    Designer's bell checkbox (``params`` bit 15 on relays and flags) marks
    an object meant for a single press; the Ampio app renders it as a
    press-only button instead of a toggle. The bit is served to both
    account tiers, so it may decide the platform. Bell wins over the relay
    Matter tag: press-only display intent makes a toggle entity wrong
    however the output is tagged.
    """
    return obj.bell


def build_buttons(data: AmpioData, obj: AmpioObject) -> list[AmpioButton]:
    """The button platform's entities for one object."""
    return [AmpioButton(data, obj)] if is_button(obj) else []


def build_identify_buttons(
    data: AmpioData, module_id: int
) -> list[AmpioIdentifyButton]:
    """The button platform's entities for one module device."""
    return [AmpioIdentifyButton(data, module_id)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the button platform; the runtime data builds and keeps its entities."""
    entry.runtime_data.async_add_platform(build_buttons, async_add_entities)
    entry.runtime_data.async_add_module_platform(
        build_identify_buttons, async_add_entities
    )


class AmpioButton(AmpioEntity, ButtonEntity):
    """A press-only control backed by a bell-marked Ampio object."""

    _attr_translation_key = "bell"

    @override
    async def async_press(self) -> None:
        """Send the single press the bell object is meant for.

        A configured Designer time makes the press a timed pulse; without
        one the press latches, matching the app. A Designer read-only
        object raises instead of sending a write the M-SERV would
        silently drop.
        """
        obj = self._object
        if obj is not None and obj.read_only:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="read_only_object",
            )
        await async_turn_on_honoring_pulse(self._data.client, obj, self._object_id)


class AmpioIdentifyButton(AmpioPinnedEntity, ButtonEntity):
    """Lights a module's CAN LED, so that the module can be found by eye.

    The Designer's "Identify device" button. The frame rides the CAN write
    tree, which answers the administrator login alone, and the module
    holds identify until a stop frame, which this entity sends after
    ``IDENTIFY_HOLD_SECONDS``. No readback exists, so the state is never
    more than the connection.
    """

    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, data: AmpioData, module_id: int) -> None:
        """Attach to the module device of Designer row ``module_id``.

        The row id keys the module device on both account tiers, and it is
        the id the library addresses the frame by, so it is the whole key.
        """
        self._data = data
        self._module_id = module_id
        self._key = f"module_{module_id}_identify"
        self._attr_unique_id = self._key
        self._attr_device_info = DeviceInfo(identifiers={module_identifier(module_id)})
        self._cancel_stop: Callable[[], None] | None = None

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the connection, which is the one thing the state reads."""
        self.async_on_remove(
            self._data.client.subscribe(
                self._connection_changed, of=AvailabilityChanged
            )
        )

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Send a pending stop now, so that an unload leaves no LED lit."""
        if self._cancel_pending_stop():
            await self._async_stop()

    @callback
    def _connection_changed(self, event: AvailabilityChanged) -> None:
        """Write state when the connection comes up or goes down."""
        self.async_write_ha_state()

    @property
    @override
    def available(self) -> bool:
        """Available while the broker is connected, on both account tiers."""
        return self._data.client.available

    @override
    async def async_press(self) -> None:
        """Send the identify start, and schedule the stop.

        A standard login is rejected before any publish, because the raw
        write tree is not served to it. A row the catalogue cannot address
        surfaces as an error with a message rather than a bare ValueError.
        """
        client = self._data.client
        if client.access_tier is not AccessTier.ADMIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="identify_needs_admin"
            )
        try:
            await client.identify(self._module_id)
        except ValueError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="module_not_addressable"
            ) from err
        self._cancel_pending_stop()
        self._cancel_stop = async_call_later(
            self.hass, IDENTIFY_HOLD_SECONDS, self._async_stop
        )

    @callback
    def _cancel_pending_stop(self) -> bool:
        """Cancel a scheduled stop, and say whether one was pending."""
        if self._cancel_stop is None:
            return False
        self._cancel_stop()
        self._cancel_stop = None
        return True

    async def _async_stop(self, _now: datetime | None = None) -> None:
        """Send the identify stop; a stop that fails leaves the LED lit, and says so."""
        self._cancel_stop = None
        try:
            await self._data.client.identify_stop(self._module_id)
        except AmpioConnectionError, AmpioTimeoutError, ValueError, RuntimeError:
            _LOGGER.warning(
                "Could not send the identify stop to Ampio module %s; its LED "
                "stays lit until Ampio Designer sends one or the module restarts",
                self._module_id,
            )
