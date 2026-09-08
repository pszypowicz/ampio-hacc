"""Sensor platform for the Ampio integration."""

from typing import override

from ampio_mqtt import AmpioObject, OutputKind, SensorKind

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    EntityCategory,
    UnitOfPressure,
    UnitOfRatio,
    UnitOfSoundPressure,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .button import is_button
from .data import AmpioConfigEntry, AmpioData
from .entity import AmpioEntity
from .light import is_light
from .switch import is_switch
from .units import device_class_for, state_class_for

PARALLEL_UPDATES = 0

# Descriptions for the sensor kinds the library can classify, keyed by
# ``SensorKind.key``. Objects classified into any other kind are not exposed.
SENSOR_DESCRIPTIONS: dict[str, SensorEntityDescription] = {
    description.key: description
    for description in (
        SensorEntityDescription(
            key="temperature",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
        ),
        SensorEntityDescription(
            key="humidity",
            device_class=SensorDeviceClass.HUMIDITY,
            native_unit_of_measurement=PERCENTAGE,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
        ),
        SensorEntityDescription(
            key="pressure_abs",
            translation_key="pressure_abs",
            device_class=SensorDeviceClass.ATMOSPHERIC_PRESSURE,
            native_unit_of_measurement=UnitOfPressure.HPA,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
        ),
        SensorEntityDescription(
            key="pressure_rel",
            translation_key="pressure_rel",
            device_class=SensorDeviceClass.PRESSURE,
            native_unit_of_measurement=UnitOfPressure.HPA,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
        ),
        SensorEntityDescription(
            key="loudness",
            translation_key="loudness",
            device_class=SensorDeviceClass.SOUND_PRESSURE,
            native_unit_of_measurement=UnitOfSoundPressure.DECIBEL,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
        ),
        SensorEntityDescription(
            key="illuminance",
            device_class=SensorDeviceClass.ILLUMINANCE,
            native_unit_of_measurement=LIGHT_LUX,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
        ),
        SensorEntityDescription(
            key="iaq",
            device_class=SensorDeviceClass.AQI,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
        ),
        SensorEntityDescription(
            key="co2",
            translation_key="co2",
            device_class=SensorDeviceClass.CO2,
            native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
        ),
    )
}

# The open key family of the integer sensor slots (bit 8, bit 16, sbit 16,
# bit 32), the shape an M-CON-485 gives a Modbus reading. The kind fixes
# no unit, so the entity reads the one Designer stores on the object. The
# other open family, ``analog_``, stays excluded: a linear input with an
# interpretation the library does not know is a gap in the classification
# tables, not a Designer choice.
VALUE_KEY_PREFIX = "value_"


# Designer's per-object time, shown where the integration honors it. The
# M-SERV never applies the time server-side, so this is the length of the
# pulse a turn-on write sends - the one behavior a user cannot otherwise
# see from Home Assistant.
PULSE_TIME_DESCRIPTION = SensorEntityDescription(
    key="pulse_time",
    translation_key="pulse_time",
    device_class=SensorDeviceClass.DURATION,
    native_unit_of_measurement=UnitOfTime.MILLISECONDS,
    suggested_unit_of_measurement=UnitOfTime.SECONDS,
    suggested_display_precision=1,
    entity_category=EntityCategory.DIAGNOSTIC,
)


def pulse_applies(obj: AmpioObject) -> bool:
    """Whether the object's turn-on write honors the Designer time.

    The ``czas`` column rides every component type and means other things
    elsewhere (a cover's travel time), so the diagnostic exists only for
    the populations whose writes send the pulse. RGBW outputs are
    excluded: ``set_colors`` has no timed form.
    """
    if obj.pulse_ms <= 0:
        return False
    if is_button(obj) or is_switch(obj):
        return True
    return is_light(obj) and not (isinstance(obj.kind, OutputKind) and obj.kind.color)


def build_sensors(data: AmpioData, obj: AmpioObject) -> list[SensorEntity]:
    """The sensor platform's entities for one object: a reading, a pulse time, or both."""
    entities: list[SensorEntity] = []
    if pulse_applies(obj):
        entities.append(AmpioPulseTimeSensor(data, obj))
    if isinstance(kind := obj.kind, SensorKind):
        if (description := SENSOR_DESCRIPTIONS.get(kind.key)) is not None:
            entities.append(AmpioSensor(data, obj, description))
        elif kind.key.startswith(VALUE_KEY_PREFIX):
            entities.append(AmpioValueSensor(data, obj))
    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the sensor platform; the runtime data builds and keeps its entities."""
    entry.runtime_data.async_add_platform(build_sensors, async_add_entities)


class AmpioSensor(AmpioEntity, SensorEntity):
    """A sensor backed by an Ampio object."""

    def __init__(
        self,
        data: AmpioData,
        obj: AmpioObject,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(data, obj)
        self.entity_description = description

    @property
    @override
    def native_value(self) -> float | None:
        """The current reading, or None when missing or non-numeric."""
        if (obj := self._object) is None:
            return None
        return obj.numeric_value


class AmpioValueSensor(AmpioEntity, SensorEntity):
    """An integer sensor slot: a bit 8, bit 16, sbit 16, or bit 32 object.

    The kind fixes no unit, so every property reads what Designer stores on
    the object, and a catalogue push re-classifies the entity on its next
    write. The M-SERV applies Designer's "Divide by" before it publishes,
    so the value is served as is.
    """

    @property
    @override
    def native_value(self) -> float | None:
        """The current reading, or None when missing or non-numeric."""
        if (obj := self._object) is None:
            return None
        return obj.numeric_value

    @property
    @override
    def native_unit_of_measurement(self) -> str | None:
        """The unit Designer stores on the object, or None."""
        if (obj := self._object) is None:
            return None
        return obj.unit

    @property
    @override
    def device_class(self) -> SensorDeviceClass | None:
        """The device class the Designer unit implies, or None."""
        return device_class_for(self.native_unit_of_measurement)

    @property
    @override
    def state_class(self) -> SensorStateClass | None:
        """A measurement or a running total once Designer gives the slot a unit.

        Without a unit the slot is a plain number, and it keeps no
        long-term statistics: a Modbus register without a unit is as often
        a status word as a reading.
        """
        if self.native_unit_of_measurement is None:
            return None
        return state_class_for(self.device_class)

    @property
    @override
    def suggested_display_precision(self) -> int | None:
        """The decimals Designer's string format fixes, or None."""
        if (obj := self._object) is None:
            return None
        return obj.decimals


class AmpioPulseTimeSensor(AmpioEntity, SensorEntity):
    """The Designer pulse length of a button, switch, or light object."""

    entity_description = PULSE_TIME_DESCRIPTION

    def __init__(self, data: AmpioData, obj: AmpioObject) -> None:
        """Initialize with a suffixed key beside the main entity."""
        super().__init__(data, obj, key_suffix="_pulse")
        # The base class silences a named object's primary entity in favor
        # of the device name; the diagnostic keeps its translated name
        # beside it.
        if hasattr(self, "_attr_name"):
            del self._attr_name

    @property
    @override
    def native_value(self) -> int | None:
        """The configured pulse length, or None once the object is gone."""
        if (obj := self._object) is None:
            return None
        return obj.pulse_ms
