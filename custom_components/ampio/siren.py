"""Siren platform for the Ampio integration: the panel buzzer."""

from collections.abc import Callable
from datetime import datetime
import logging
from typing import Any, Final, override

from ampio_mqtt import AmpioConnectionError, AmpioTimeoutError, ModuleFunction
import voluptuous as vol

from homeassistant.components.siren import (
    ATTR_DURATION,
    ATTR_TONE,
    SirenEntity,
    SirenEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import VolDictType

from .const import DOMAIN
from .data import AmpioConfigEntry, AmpioData
from .entity import AmpioModuleEntity

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# The Designer default, and the loudest: the piezo resonates near 2.4 kHz
# and the fundamental is 16576 Hz over the tone plus one.
DEFAULT_TONE: Final = 6
# The sequence frame's per-step ceiling, in seconds. A latched call asks
# for one step this long and repeats it, which is a continuous tone.
MAX_STEP_SECONDS: Final = 655.35

# The sequence frame's own fields, at the ranges the wire accepts. Times
# are 10 ms ticks on the wire, so the ceiling is 655.35 s; tone 0 is a
# silent rest, which is what makes a pip pattern one frame.
BUZZ_PATTERN_SCHEMA: VolDictType = {
    vol.Required("tone"): vol.All(vol.Coerce(int), vol.Range(min=0, max=31)),
    vol.Required("seconds"): vol.All(
        vol.Coerce(float), vol.Range(min=0, max=MAX_STEP_SECONDS)
    ),
    vol.Optional("tone2", default=0): vol.All(
        vol.Coerce(int), vol.Range(min=0, max=31)
    ),
    vol.Optional("seconds2", default=0.0): vol.All(
        vol.Coerce(float), vol.Range(min=0, max=MAX_STEP_SECONDS)
    ),
    vol.Optional("cycles", default=1): vol.All(
        vol.Coerce(int), vol.Range(min=0, max=254)
    ),
    vol.Optional("delay", default=0.0): vol.All(
        vol.Coerce(float), vol.Range(min=0, max=MAX_STEP_SECONDS)
    ),
}


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
    module = data.module_row(module_id)
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
    entity_platform.async_get_current_platform().async_register_entity_service(
        "buzz_pattern", BUZZ_PATTERN_SCHEMA, "async_buzz_pattern"
    )


class AmpioBuzzer(AmpioModuleEntity, SirenEntity):
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
        """Attach to the module device, silent and with no stop pending."""
        super().__init__(data, module_id, key_suffix="buzzer")
        self._attr_is_on = False
        self._cancel_stop: Callable[[], None] | None = None

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Silence a sounding panel, so that an unload leaves nothing buzzing."""
        if self._attr_is_on:
            self._cancel_pending_stop()
            await self._async_silence()

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
        if seconds > MAX_STEP_SECONDS:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="buzz_duration_too_long",
                translation_placeholders={"maximum": str(MAX_STEP_SECONDS)},
            )
        cycles = 0 if duration is None else 1
        try:
            await self._data.client.buzz_pattern(
                self._module_id, tone=tone, seconds=seconds, cycles=cycles
            )
        except ValueError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="module_not_addressable"
            ) from err
        except (AmpioConnectionError, AmpioTimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="buzzer_command_failed"
            ) from err
        self._cancel_pending_stop()
        self._attr_is_on = True
        if duration is not None:
            self._cancel_stop = async_call_later(self.hass, seconds, self._async_expire)
        self.async_write_ha_state()

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Silence the buzzer now; a stop the broker does not carry raises.

        Unlike the unload path, a user-invoked stop must not report success
        it did not reach, so the caller learns the panel may still sound.
        """
        self._cancel_pending_stop()
        try:
            await self._data.client.buzz_stop(self._module_id)
        except (AmpioConnectionError, AmpioTimeoutError, ValueError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="buzzer_stop_failed"
            ) from err
        self._attr_is_on = False
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

        A finite sequence ends on its own, and the panel says nothing when
        it does, so the state clears on a timer covering the whole run.
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
        except (AmpioConnectionError, AmpioTimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="buzzer_command_failed"
            ) from err
        self._cancel_pending_stop()
        self._attr_is_on = True
        if cycles:
            self._cancel_stop = async_call_later(
                self.hass, delay + cycles * (seconds + seconds2), self._async_expire
            )
        self.async_write_ha_state()

    @callback
    def _cancel_pending_stop(self) -> bool:
        """Cancel a scheduled stop, and say whether one was pending."""
        if self._cancel_stop is None:
            return False
        self._cancel_stop()
        self._cancel_stop = None
        return True

    async def _async_expire(self, _now: datetime | None = None) -> None:
        """Clear the state when a timed call has run its course."""
        self._cancel_stop = None
        self._attr_is_on = False
        self.async_write_ha_state()

    async def _async_silence(self) -> None:
        """Send the stop for an unload; a failure leaves the panel sounding, and logs.

        An unload must not raise, unlike the service path in ``async_turn_off``.
        """
        self._attr_is_on = False
        try:
            await self._data.client.buzz_stop(self._module_id)
        except AmpioConnectionError, AmpioTimeoutError, ValueError:
            _LOGGER.warning(
                "Could not silence the buzzer on Ampio module %s; it sounds "
                "until Ampio Designer stops it or the panel restarts",
                self._module_id,
            )
