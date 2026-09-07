"""Cover platform for the Ampio integration."""

from typing import Any, override

from ampio_mqtt import AmpioObject, OutputKind

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .data import AmpioConfigEntry, AmpioData
from .entity import AmpioEntity

PARALLEL_UPDATES = 0


def build_covers(data: AmpioData, obj: AmpioObject) -> list[AmpioCover]:
    """The cover platform's entities for one object."""
    if isinstance(obj.kind, OutputKind) and obj.kind.cover:
        return [AmpioCover(data, obj)]
    return []


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the cover platform; the runtime data builds and keeps its entities."""
    entry.runtime_data.async_add_platform(build_covers, async_add_entities)


class AmpioCover(AmpioEntity, CoverEntity):
    """A cover backed by an Ampio output object.

    Ampio and Home Assistant share the percent convention: 0 is fully
    closed, 100 is fully open, on both the travel and the slat axis.
    """

    def __init__(self, data: AmpioData, obj: AmpioObject) -> None:
        """Initialize with the feature set the object's kind supports."""
        super().__init__(data, obj)
        kind = obj.kind
        has_position = isinstance(kind, OutputKind) and kind.position
        has_tilt = isinstance(kind, OutputKind) and kind.tilt
        features = (
            CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        )
        if has_position:
            features |= CoverEntityFeature.SET_POSITION
        if has_tilt:
            features |= (
                CoverEntityFeature.SET_TILT_POSITION
                | CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.STOP_TILT
            )
        self._attr_supported_features = features
        self._attr_device_class = (
            CoverDeviceClass.BLIND if has_tilt else CoverDeviceClass.SHUTTER
        )

    @property
    @override
    def current_cover_position(self) -> int | None:
        """Travel percent, or None without a position axis or feedback."""
        if (obj := self._object) is None:
            return None
        return obj.position

    @property
    @override
    def current_cover_tilt_position(self) -> int | None:
        """Slat angle percent; the library populates it only for tilt-capable covers."""
        if (obj := self._object) is None:
            return None
        return obj.lammel

    @property
    @override
    def is_closed(self) -> bool | None:
        """Whether the cover is fully closed; None without position feedback."""
        if (position := self.current_cover_position) is None:
            return None
        return position == 0

    @override
    async def async_open_cover(self, **kwargs: Any) -> None:
        """Drive the cover fully open."""
        await self._data.client.open(self._object_id)

    @override
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Drive the cover fully closed."""
        await self._data.client.close(self._object_id)

    @override
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Halt travel wherever the cover is."""
        await self._data.client.stop(self._object_id)

    @override
    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Drive the cover to the requested percent."""
        await self._data.client.set_roller_pos(self._object_id, kwargs[ATTR_POSITION])

    @override
    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Set the slat angle to the requested percent."""
        await self._data.client.set_roller_lamella(
            self._object_id, kwargs[ATTR_TILT_POSITION]
        )

    @override
    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open the slats fully."""
        await self._data.client.set_roller_lamella(self._object_id, 100)

    @override
    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close the slats fully."""
        await self._data.client.set_roller_lamella(self._object_id, 0)

    @override
    async def async_stop_cover_tilt(self, **kwargs: Any) -> None:
        """Halt slat rotation; the stop verb halts either axis."""
        await self._data.client.stop(self._object_id)
