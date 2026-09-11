"""Siren platform for the Ampio integration: the panel buzzer."""

from collections.abc import Callable
from datetime import datetime
import logging
from typing import Any, Final, override

from ampio_mqtt import (
    AmpioConnectionError,
    AmpioTimeoutError,
    AvailabilityChanged,
    ModuleFunction,
)

from homeassistant.components.siren import (
    ATTR_DURATION,
    ATTR_TONE,
    SirenEntity,
    SirenEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .data import AmpioConfigEntry, AmpioData, module_identifier
from .entity import AmpioPinnedEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# The Designer default, and the loudest: the piezo resonates near 2.4 kHz
# and the fundamental is 16576 Hz over the tone plus one.
DEFAULT_TONE: Final = 6
# The sequence frame's per-step ceiling, in seconds. A latched call asks
# for one step this long and repeats it, which is a continuous tone.
MAX_STEP_SECONDS: Final = 655.35


def build_buzzers(data: AmpioData, module_id: int) -> list[AmpioBuzzer]:
    """The siren platform's entities for one module device.

    A standard account receives no module catalogue, so the capability is
    unknowable there. This answers for every row on that tier, which only
    the withheld enumeration ever reads: the tier gate means nothing is
    built from it, and a bare capability check would leave an orphaned
    buzzer record in the repair card meant for a Designer deletion.
    """
    if not data.is_admin:
        return [AmpioBuzzer(data, module_id)]
    module = data.client.modules.get(module_id)
    if module is None or ModuleFunction.BUZZER not in module.capabilities:
        return []
    return [AmpioBuzzer(data, module_id)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the siren platform; the runtime data builds and keeps its entities."""
    entry.runtime_data.async_add_module_platform(
        build_buzzers, async_add_entities, admin_only=True
    )


class AmpioBuzzer(AmpioPinnedEntity, SirenEntity):
    """The piezo buzzer on an Ampio touch panel.

    The frame rides the CAN write tree, which answers the administrator
    login alone, so this entity is built on that account alone. The panel
    confirms nothing on the bus, so the state is what this entity last
    asked for: a timed call clears it on a timer, and a stop goes out on
    unload so no panel is left sounding.
    """

    _attr_translation_key = "buzzer"
    _attr_supported_features = (
        SirenEntityFeature.TURN_ON
        | SirenEntityFeature.TURN_OFF
        | SirenEntityFeature.TONES
        | SirenEntityFeature.DURATION
    )
    # The wire's own numbering, which is also the library's parameter and
    # what Ampio Designer shows. The loudness table lives in the library's
    # panel-writes notes.
    _attr_available_tones = list(range(1, 32))

    def __init__(self, data: AmpioData, module_id: int) -> None:
        """Attach to the module device of Designer row ``module_id``."""
        self._data = data
        self._module_id = module_id
        self._key = f"module_{module_id}_buzzer"
        self._attr_unique_id = self._key
        self._attr_device_info = DeviceInfo(identifiers={module_identifier(module_id)})
        self._attr_is_on = False
        self._cancel_stop: Callable[[], None] | None = None

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the connection, which is the one thing availability reads."""
        self.async_on_remove(
            self._data.client.subscribe(
                self._connection_changed, of=AvailabilityChanged
            )
        )

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Silence a sounding panel, so that an unload leaves nothing buzzing."""
        if self._attr_is_on:
            self._cancel_pending_stop()
            await self._async_silence()

    @callback
    def _connection_changed(self, event: AvailabilityChanged) -> None:
        """Write state when the connection comes up or goes down."""
        self.async_write_ha_state()

    @property
    @override
    def available(self) -> bool:
        """Available while the broker is connected."""
        return self._data.client.available

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Sound the buzzer, for a time or until stopped.

        Always the sequence frame: the simple one caps at 2.55 s and a
        siren duration may exceed it. Without a duration the sequence
        repeats, which is a tone that holds until the stop.
        """
        tone = int(kwargs.get(ATTR_TONE, DEFAULT_TONE))
        duration = kwargs.get(ATTR_DURATION)
        seconds = MAX_STEP_SECONDS if duration is None else float(duration)
        cycles = 0 if duration is None else 1
        try:
            await self._data.client.buzz_pattern(
                self._module_id, tone=tone, seconds=seconds, cycles=cycles
            )
        except ValueError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="module_not_addressable"
            ) from err
        self._cancel_pending_stop()
        self._attr_is_on = True
        if duration is not None:
            self._cancel_stop = async_call_later(self.hass, seconds, self._async_expire)
        self.async_write_ha_state()

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Silence the buzzer now."""
        self._cancel_pending_stop()
        await self._async_silence()
        self.async_write_ha_state()

    async def async_buzz_pattern(
        self,
        tone: int,
        seconds: float,
        tone2: int,
        seconds2: float,
        cycles: int,
        delay: float,
    ) -> None:
        """Play the frame's own two-slot sequence.

        Tone 0 is a silent rest, so three pips are one tone, one rest, three
        cycles. ``cycles`` 0 repeats until a stop.
        """
        try:
            await self._data.client.buzz_pattern(
                self._module_id,
                tone=tone,
                seconds=seconds,
                tone2=tone2,
                seconds2=seconds2,
                cycles=cycles,
                delay=delay,
            )
        except ValueError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="module_not_addressable"
            ) from err
        self._cancel_pending_stop()
        self._attr_is_on = True
        self.async_write_ha_state()

    @callback
    def _cancel_pending_stop(self) -> None:
        """Cancel a scheduled stop, if one is waiting."""
        if self._cancel_stop is not None:
            self._cancel_stop()
            self._cancel_stop = None

    async def _async_expire(self, _now: datetime | None = None) -> None:
        """Clear the state when a timed call has run its course."""
        self._cancel_stop = None
        self._attr_is_on = False
        self.async_write_ha_state()

    async def _async_silence(self) -> None:
        """Send the stop; one that fails leaves the panel sounding, and says so."""
        self._attr_is_on = False
        try:
            await self._data.client.buzz_stop(self._module_id)
        except AmpioConnectionError, AmpioTimeoutError, ValueError:
            _LOGGER.warning(
                "Could not silence the buzzer on Ampio module %s; it sounds "
                "until Ampio Designer stops it or the panel restarts",
                self._module_id,
            )
