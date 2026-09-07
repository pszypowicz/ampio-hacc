"""The Home Assistant vocabulary for the unit Designer stores on an integer sensor slot."""

from typing import Final

from homeassistant.components.sensor import (
    DEVICE_CLASS_STATE_CLASSES,
    DEVICE_CLASS_UNITS,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfPressure, UnitOfTemperature

# Units that core lists under two device classes, settled for the class a
# meter or a probe means. Each key must stay ambiguous in core's table:
# the test suite fails when a core release settles one, so the entry can
# go.
UNIT_DEVICE_CLASS_OVERRIDES: Final[dict[str, SensorDeviceClass]] = {
    str(UnitOfEnergy.WATT_HOUR): SensorDeviceClass.ENERGY,
    str(UnitOfEnergy.KILO_WATT_HOUR): SensorDeviceClass.ENERGY,
    str(UnitOfEnergy.MEGA_WATT_HOUR): SensorDeviceClass.ENERGY,
    str(UnitOfTemperature.CELSIUS): SensorDeviceClass.TEMPERATURE,
    str(UnitOfTemperature.FAHRENHEIT): SensorDeviceClass.TEMPERATURE,
    str(UnitOfPressure.PA): SensorDeviceClass.PRESSURE,
    str(UnitOfPressure.HPA): SensorDeviceClass.PRESSURE,
    str(UnitOfPressure.KPA): SensorDeviceClass.PRESSURE,
    str(UnitOfPressure.BAR): SensorDeviceClass.PRESSURE,
    str(UnitOfPressure.MBAR): SensorDeviceClass.PRESSURE,
}


def _unique_unit_classes() -> dict[str, SensorDeviceClass]:
    """Every unit core lists under exactly one device class, mapped to it."""
    owners: dict[str, set[SensorDeviceClass]] = {}
    for device_class, units in DEVICE_CLASS_UNITS.items():
        for unit in units:
            if unit is not None:
                owners.setdefault(str(unit), set()).add(device_class)
    return {
        unit: next(iter(found)) for unit, found in owners.items() if len(found) == 1
    }


# The unit table: core's unique units, then the overrides on top.
UNIT_DEVICE_CLASS: Final[dict[str, SensorDeviceClass]] = {
    **_unique_unit_classes(),
    **UNIT_DEVICE_CLASS_OVERRIDES,
}


def device_class_for(unit: str | None) -> SensorDeviceClass | None:
    """The device class a Designer unit implies, or None."""
    return UNIT_DEVICE_CLASS.get(unit) if unit is not None else None


def state_class_for(
    device_class: SensorDeviceClass | None,
) -> SensorStateClass | None:
    """The state class of a reading of ``device_class``.

    A measurement wherever core allows one, else a running total (energy,
    gas, water, volume), else None for a class with no numeric state.
    """
    if device_class is None:
        return SensorStateClass.MEASUREMENT
    allowed = DEVICE_CLASS_STATE_CLASSES.get(device_class, set())
    if SensorStateClass.MEASUREMENT in allowed:
        return SensorStateClass.MEASUREMENT
    if SensorStateClass.TOTAL_INCREASING in allowed:
        return SensorStateClass.TOTAL_INCREASING
    return None
