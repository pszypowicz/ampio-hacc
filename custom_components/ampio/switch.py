"""Switch platform for the Ampio integration."""

from typing import Any, override

from ampio_mqtt import AmpioObject, InputKind, OutputKind

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .data import AmpioConfigEntry, AmpioData
from .entity import AmpioEntity, async_turn_on_honoring_pulse
from .light import LIGHT_MATTER_TYPES

PARALLEL_UPDATES = 0

# Matter "Plugs" device types: a relay tagged as a plug-in unit reads as an
# outlet. Every other non-light relay reads as a generic switch.
PLUG_MATTER_TYPES = frozenset({0x010A, 0x010B})


def is_switch(obj: AmpioObject) -> bool:
    """Whether the object belongs to the switch platform.

    Two populations land here. A relay whose catalogue-column Matter tag
    is not in ``LIGHT_MATTER_TYPES`` - the complement of the light
    platform's relay rule, on the same tier-independent source. And any
    input kind that declares itself switchable: the writable flags, a
    promised target of the client's switch verbs. Bell-marked objects
    belong to the button platform in both populations.
    """
    if obj.bell:
        return False
    if isinstance(kind := obj.kind, InputKind):
        return kind.switchable
    if not isinstance(kind, OutputKind):
        return False
    return kind.key == "relay" and obj.matter_device_type not in LIGHT_MATTER_TYPES


def build_switches(data: AmpioData, obj: AmpioObject) -> list[AmpioSwitch]:
    """The switch platform's entities for one object."""
    return [AmpioSwitch(data, obj)] if is_switch(obj) else []


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the switch platform; the runtime data builds and keeps its entities."""
    entry.runtime_data.async_add_platform(build_switches, async_add_entities)


class AmpioSwitch(AmpioEntity, SwitchEntity):
    """A switch backed by an Ampio relay or writable flag object."""

    def __init__(self, data: AmpioData, obj: AmpioObject) -> None:
        """Initialize with the device class the Matter tag suggests."""
        super().__init__(data, obj)
        if isinstance(obj.kind, InputKind):
            # A writable flag has no socket semantics; unnamed flags take
            # the translated name.
            self._attr_translation_key = "flag"
        else:
            self._attr_device_class = (
                SwitchDeviceClass.OUTLET
                if obj.matter_device_type in PLUG_MATTER_TYPES
                else SwitchDeviceClass.SWITCH
            )

    @property
    @override
    def is_on(self) -> bool | None:
        """Whether the object is on, or None once it is gone."""
        if (obj := self._object) is None:
            return None
        return obj.is_on

    def _check_writable(self) -> None:
        """Reject a write the M-SERV would silently drop.

        Designer's read-only marker is enforced server-side on both account
        tiers; the entity keeps its platform because the checkbox can change
        at any time.
        """
        if (obj := self._object) is not None and obj.read_only:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="read_only_object",
            )

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the object on, timed when Designer configures a pulse."""
        self._check_writable()
        await async_turn_on_honoring_pulse(
            self._data.client, self._object, self._object_id
        )

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the object off."""
        self._check_writable()
        await self._data.client.turn_off(self._object_id)
