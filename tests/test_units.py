"""Tests for the unit vocabulary of the integer sensor slots."""

import pytest

from custom_components.ampio.units import (
    UNIT_DEVICE_CLASS_OVERRIDES,
    device_class_for,
    state_class_for,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.components.sensor.const import DEVICE_CLASS_UNITS


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        pytest.param("A", SensorDeviceClass.CURRENT, id="unique-current"),
        pytest.param("V", SensorDeviceClass.VOLTAGE, id="unique-voltage"),
        pytest.param("W", SensorDeviceClass.POWER, id="unique-power"),
        pytest.param("kW", SensorDeviceClass.POWER, id="unique-kilo-power"),
        pytest.param("lx", SensorDeviceClass.ILLUMINANCE, id="unique-illuminance"),
        pytest.param("kWh", SensorDeviceClass.ENERGY, id="override-energy"),
        pytest.param("°C", SensorDeviceClass.TEMPERATURE, id="override-temperature"),
        pytest.param("hPa", SensorDeviceClass.PRESSURE, id="override-pressure"),
        pytest.param("%", None, id="ambiguous-without-override"),
        pytest.param("m³", None, id="ambiguous-volume"),
        pytest.param("A/100", None, id="unknown-to-core"),
        pytest.param(None, None, id="no-unit"),
    ],
)
def test_device_class_for(unit: str | None, expected: SensorDeviceClass | None) -> None:
    """A unique core unit maps, an override maps, and anything else reads None."""
    assert device_class_for(unit) == expected


def test_override_keys_are_ambiguous_in_core() -> None:
    """An override exists only where core lists the unit under two classes.

    A core release that settles one turns the override into dead weight,
    and this test then asks for its removal. The chosen class must also
    still own the unit, or core would reject the pairing.
    """
    for unit in UNIT_DEVICE_CLASS_OVERRIDES:
        owners = {
            device_class
            for device_class, units in DEVICE_CLASS_UNITS.items()
            if unit in units
        }
        assert len(owners) > 1, unit
        assert UNIT_DEVICE_CLASS_OVERRIDES[unit] in owners, unit


@pytest.mark.parametrize(
    ("device_class", "expected"),
    [
        pytest.param(
            SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, id="energy"
        ),
        pytest.param(
            SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, id="voltage"
        ),
        pytest.param(
            SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT, id="duration"
        ),
        pytest.param(None, SensorStateClass.MEASUREMENT, id="no-class"),
    ],
)
def test_state_class_for(
    device_class: SensorDeviceClass | None, expected: SensorStateClass
) -> None:
    """A measurement wherever core allows one, else a running total."""
    assert state_class_for(device_class) == expected
