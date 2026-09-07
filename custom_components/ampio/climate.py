"""Climate platform for the Ampio integration."""

from typing import Any, override

from ampio_mqtt import AmpioObject, ThermostatKind, ThermostatState

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .data import AmpioConfigEntry, AmpioData
from .entity import AmpioEntity

PARALLEL_UPDATES = 0

# The regulator's operating modes: wire letter (ampio_mqtt.HEATING_MODES) to
# preset name, following the Designer vocabulary.
PRESET_BY_MODE: dict[str, str] = {
    "A": "auto",
    "S": "schedule",
    "M": "manual",
    "H": "holiday",
}
MODE_BY_PRESET: dict[str, str] = {
    preset: mode for mode, preset in PRESET_BY_MODE.items()
}


def build_thermostats(data: AmpioData, obj: AmpioObject) -> list[AmpioClimate]:
    """The climate platform's entities for one object."""
    return [AmpioClimate(data, obj)] if isinstance(obj.kind, ThermostatKind) else []


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the climate platform; the runtime data builds and keeps its entities."""
    entry.runtime_data.async_add_platform(build_thermostats, async_add_entities)


class AmpioClimate(AmpioEntity, ClimateEntity):
    """A heating regulator backed by an Ampio ``reg`` object.

    State reads the regulator's climate readback: measured and target
    temperatures, the operating-mode letter (exposed as a preset), and
    the cooling flag. The running flag drives the action.
    """

    _attr_preset_modes = list(PRESET_BY_MODE.values())
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    @property
    def _thermostat(self) -> ThermostatState | None:
        """The regulator's readback, or None before the first rich push."""
        if (obj := self._object) is None:
            return None
        return obj.thermostat

    @property
    @override
    def hvac_mode(self) -> HVACMode:
        """COOL while the readback's cooling flag is set, HEAT otherwise."""
        if (thermostat := self._thermostat) is not None and thermostat.cooling:
            return HVACMode.COOL
        return HVACMode.HEAT

    @property
    @override
    def hvac_modes(self) -> list[HVACMode]:
        """The single mode the readback currently selects."""
        return [self.hvac_mode]

    @property
    @override
    def current_temperature(self) -> float | None:
        """The measured temperature from the readback."""
        if (thermostat := self._thermostat) is None:
            return None
        return thermostat.measure_temp

    @property
    @override
    def target_temperature(self) -> float | None:
        """The setpoint from the readback."""
        if (thermostat := self._thermostat) is None:
            return None
        return thermostat.set_temperature

    @property
    @override
    def preset_mode(self) -> str | None:
        """The operating mode's preset name; None for an unknown letter."""
        if (thermostat := self._thermostat) is None or thermostat.mode is None:
            return None
        return PRESET_BY_MODE.get(thermostat.mode)

    @property
    @override
    def hvac_action(self) -> HVACAction | None:
        """Idle while stopped; cooling or heating by the readback flag."""
        if (obj := self._object) is None:
            return None
        if not obj.is_on:
            return HVACAction.IDLE
        if (thermostat := obj.thermostat) is not None and thermostat.cooling:
            return HVACAction.COOLING
        return HVACAction.HEATING

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Send the setpoint; the state follows the readback echo."""
        await self._data.client.set_temperature(
            self._object_id, kwargs[ATTR_TEMPERATURE]
        )

    @override
    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Send the operating mode matching the chosen preset."""
        await self._data.client.set_heating_mode(
            self._object_id, MODE_BY_PRESET[preset_mode]
        )
