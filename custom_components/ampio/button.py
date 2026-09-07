"""Button platform for the Ampio integration."""

from typing import override

from ampio_mqtt import AmpioObject

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .data import AmpioConfigEntry, eligible_objects
from .entity import AmpioEntity, async_turn_on_honoring_pulse

PARALLEL_UPDATES = 0


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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Ampio buttons from the discovery-time object catalogue."""
    data = entry.runtime_data
    async_add_entities(
        AmpioButton(data, obj)
        for obj in eligible_objects(data.client)
        if is_button(obj)
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
