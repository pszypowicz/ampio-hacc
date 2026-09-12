"""Button platform for the Ampio integration."""

from collections.abc import Callable
from datetime import datetime
import logging
from typing import Final, override

from ampio_mqtt import AmpioConnectionError, AmpioObject, AmpioTimeoutError

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .data import AmpioConfigEntry, AmpioData
from .entity import AmpioEntity, AmpioModuleEntity, async_turn_on_honoring_pulse

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
        build_identify_buttons, async_add_entities, admin_only=True
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


class AmpioIdentifyButton(AmpioModuleEntity, ButtonEntity):
    """Lights a module's CAN LED, so that the module can be found by eye.

    The Designer's "Identify device" button. The frame rides the CAN write
    tree, which answers the administrator login alone, so this entity is
    built on that account alone. The module holds identify until a stop
    frame, which this entity sends after ``IDENTIFY_HOLD_SECONDS``. No
    readback exists, so the state is never more than the connection.
    """

    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, data: AmpioData, module_id: int) -> None:
        """Attach to the module device, and start with no stop pending.

        The identify frame is addressed by the Designer row id, which is the
        fact that makes it the button's whole key.
        """
        super().__init__(data, module_id, key_suffix="identify")
        self._cancel_stop: Callable[[], None] | None = None

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Send a pending stop now, so that an unload leaves no LED lit."""
        if self._cancel_pending_stop():
            await self._async_stop()

    @override
    async def async_press(self) -> None:
        """Send the identify start, and schedule the stop.

        A row the catalogue cannot address surfaces as an error with a
        message rather than a bare ValueError.
        """
        try:
            await self._data.client.identify(self._module_id)
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
        except AmpioConnectionError, AmpioTimeoutError, ValueError:
            _LOGGER.warning(
                "Could not send the identify stop to Ampio module %s; its LED "
                "stays lit until Ampio Designer sends one or the module restarts",
                self._module_id,
            )
