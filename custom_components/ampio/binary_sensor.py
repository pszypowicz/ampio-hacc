"""Binary sensor platform for the Ampio integration."""

from typing import override

from ampio_mqtt import AmpioObject, InputKind

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .data import AmpioConfigEntry, AmpioData
from .entity import AmpioEntity, eligible_objects

PARALLEL_UPDATES = 0

# Descriptions for the read-only input kinds, keyed by ``InputKind.key``.
# Switchable inputs (the writable flags) belong to the switch platform, the
# system kinds (the M-SERV's own detection and simulation objects) never
# reach a platform, and objects classified into any other kind are not
# exposed.
BINARY_SENSOR_DESCRIPTIONS: dict[str, BinarySensorEntityDescription] = {
    description.key: description
    for description in (
        BinarySensorEntityDescription(
            key="wej",
            translation_key="input",
        ),
    )
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Ampio binary sensors from the discovery-time object catalogue."""
    data = entry.runtime_data
    entities: list[AmpioBinarySensor] = []
    for obj in eligible_objects(data.client):
        if not isinstance(kind := obj.kind, InputKind):
            continue
        if (description := BINARY_SENSOR_DESCRIPTIONS.get(kind.key)) is None:
            continue
        entities.append(AmpioBinarySensor(data, obj, description))
    async_add_entities(entities)


class AmpioBinarySensor(AmpioEntity, BinarySensorEntity):
    """A binary sensor backed by an Ampio input object."""

    def __init__(
        self,
        data: AmpioData,
        obj: AmpioObject,
        description: BinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(data, obj)
        self.entity_description = description

    @property
    @override
    def is_on(self) -> bool | None:
        """Whether the input reads on, or None once the object is gone."""
        if (obj := self._object) is None:
            return None
        return obj.is_on
