"""Light platform for the Ampio integration."""

from typing import Any, override

from ampio_mqtt import AmpioObject, OutputKind

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGBW_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .data import AmpioConfigEntry, AmpioData, eligible_objects
from .entity import AmpioEntity, async_turn_on_honoring_pulse

PARALLEL_UPDATES = 0

# Matter "Lighting" device types from the Designer's optional per-output tag,
# mirrored into the catalogue ``type`` column both account tiers receive and
# surfaced as ``AmpioObject.matter_device_type``. A relay carrying one is
# installer intent that it drives a light; untagged relays are left for the
# switch platform.
LIGHT_MATTER_TYPES = frozenset({0x0100, 0x0101, 0x010C, 0x010D})

# A bare turn_on (no requested color) on an all-zero rgbw output raises the
# white channel instead of writing back the dark state it started from.
_DEFAULT_RGBW = (0, 0, 0, 255)


def is_light(obj: AmpioObject) -> bool:
    """Whether the object belongs to the light platform.

    ``matter_device_type`` is the catalogue-column tag - the
    tier-independent classification source. The admin-only record tag
    (``record.matter_device_type``) never feeds the partition. A
    bell-marked relay belongs to the button platform whatever its tag.
    """
    if obj.bell:
        return False
    if not isinstance(kind := obj.kind, OutputKind):
        return False
    if kind.color or kind.dimmable:
        return True
    return kind.key == "relay" and obj.matter_device_type in LIGHT_MATTER_TYPES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Ampio lights from the discovery-time object catalogue."""
    data = entry.runtime_data
    async_add_entities(
        AmpioLight(data, obj) for obj in eligible_objects(data.client) if is_light(obj)
    )


class AmpioLight(AmpioEntity, LightEntity):
    """A light backed by an Ampio output object."""

    def __init__(self, data: AmpioData, obj: AmpioObject) -> None:
        """Initialize in the one color mode the object's kind supports."""
        super().__init__(data, obj)
        kind = obj.kind
        if isinstance(kind, OutputKind) and kind.color:
            mode = ColorMode.RGBW
        elif isinstance(kind, OutputKind) and kind.dimmable:
            mode = ColorMode.BRIGHTNESS
        else:
            mode = ColorMode.ONOFF
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}

    @property
    @override
    def is_on(self) -> bool | None:
        """Whether the light is on, or None once the object is gone."""
        if (obj := self._object) is None:
            return None
        return obj.is_on

    @property
    @override
    def brightness(self) -> int | None:
        """The 0-255 level for a dimmer, the peak channel for rgbw."""
        if (obj := self._object) is None:
            return None
        if self._attr_color_mode is ColorMode.RGBW:
            return None if (rgbw := obj.rgbw) is None else max(rgbw)
        if self._attr_color_mode is ColorMode.BRIGHTNESS:
            return None if (level := obj.numeric_value) is None else int(level)
        return None

    @property
    @override
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """The four channels of an rgbw output."""
        if (obj := self._object) is None:
            return None
        return obj.rgbw

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, honoring brightness and rgbw color.

        Resolved rgbw channels that are all zero mean off; an explicit
        all-zero color is a request for darkness, and turn_off is its
        honest execution.
        """
        client = self._data.client
        if self._attr_color_mode is ColorMode.RGBW:
            rgbw: tuple[int, int, int, int] | None = kwargs.get(ATTR_RGBW_COLOR)
            if rgbw is None:
                current = self._object.rgbw if self._object else None
                rgbw = current if current and any(current) else _DEFAULT_RGBW
            if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
                peak = max(rgbw) or 255
                red, green, blue, white = (
                    channel * brightness // peak for channel in rgbw
                )
                rgbw = (red, green, blue, white)
            if not any(rgbw):
                await self.async_turn_off()
                return
            await client.set_colors(self._object_id, *rgbw)
            return
        obj = self._object
        if (
            self._attr_color_mode is ColorMode.BRIGHTNESS
            and (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None
        ):
            await client.set_value(
                self._object_id,
                brightness,
                pulse_ms=(obj.pulse_ms or None) if obj else None,
            )
            return
        await async_turn_on_honoring_pulse(client, obj, self._object_id)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off; the client routes rgbw off to setColors."""
        await self._data.client.turn_off(self._object_id)
