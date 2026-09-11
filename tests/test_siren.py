"""Tests for the panel buzzer of the Ampio integration."""

from collections.abc import Generator
from datetime import timedelta
from unittest.mock import MagicMock, patch

from ampio_mqtt import AccessTier, AvailabilityChanged
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
import voluptuous as vol

from custom_components.ampio.const import DOMAIN
from custom_components.ampio.siren import MAX_STEP_SECONDS
from homeassistant.components.siren import (
    ATTR_DURATION,
    ATTR_TONE,
    DOMAIN as SIREN_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_UNAVAILABLE,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from . import setup_integration
from .conftest import emit, with_buzzer

BUZZER_ENTITY_ID = "siren.ampio_module_17_buzzer"


@pytest.fixture
def siren_only() -> Generator[None]:
    """Limit setup to the siren platform so the assertions stay scoped."""
    with patch("custom_components.ampio.PLATFORMS", [Platform.SIREN]):
        yield


async def _turn_on(hass: HomeAssistant, **data: object) -> None:
    await hass.services.async_call(
        SIREN_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: BUZZER_ENTITY_ID, **data},
        blocking=True,
    )


async def _elapse(hass: HomeAssistant, seconds: float) -> None:
    """Let ``seconds`` pass for the expire timer, and let its task finish."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


@pytest.mark.usefixtures("siren_only")
async def test_buzzer_only_on_a_module_that_reports_one(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """The capability map decides, so a module without a buzzer gets no siren."""
    await setup_integration(hass, mock_config_entry)
    assert entity_registry.async_get(BUZZER_ENTITY_ID) is None


@pytest.mark.usefixtures("siren_only")
async def test_buzzer_exists_when_the_module_reports_one(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """A module whose capabilities carry the buzzer gets a siren."""
    with_buzzer(mock_client)

    await setup_integration(hass, mock_config_entry)

    entry = entity_registry.async_get(BUZZER_ENTITY_ID)
    assert entry is not None
    assert entry.unique_id == "module_17_buzzer"


@pytest.mark.usefixtures("siren_only")
async def test_turn_on_with_a_duration_sends_one_cycle(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A timed call is one cycle of the sequence frame, not the simple one.

    The simple frame caps at 2.55 s, and a siren duration may exceed it.
    """
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)

    await _turn_on(hass, **{ATTR_TONE: 4, ATTR_DURATION: 5})

    mock_client.buzz_pattern.assert_awaited_once_with(17, tone=4, seconds=5.0, cycles=1)
    assert hass.states.get(BUZZER_ENTITY_ID).state == "on"


@pytest.mark.usefixtures("siren_only")
async def test_turn_on_without_a_duration_latches(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """No duration means sound until stopped, which is cycles zero."""
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)

    await _turn_on(hass)

    call = mock_client.buzz_pattern.await_args
    assert call.kwargs["cycles"] == 0
    assert call.kwargs["tone"] == 6
    assert call.kwargs["seconds"] == MAX_STEP_SECONDS


@pytest.mark.usefixtures("siren_only")
async def test_timed_call_clears_state_when_duration_elapses(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The state clears itself when a timed call runs its course, not before.

    The panel stops itself at the end of its one cycle, so no stop frame
    goes out; only the local state needs to follow the timer.
    """
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)

    await _turn_on(hass, **{ATTR_DURATION: 5})
    assert hass.states.get(BUZZER_ENTITY_ID).state == "on"

    await _elapse(hass, 4)
    assert hass.states.get(BUZZER_ENTITY_ID).state == "on"

    await _elapse(hass, 6)
    assert hass.states.get(BUZZER_ENTITY_ID).state == "off"
    mock_client.buzz_stop.assert_not_called()


@pytest.mark.usefixtures("siren_only")
async def test_turn_off_stops_the_buzzer(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Stopping sends the library's stop pair, clears the state, and cancels the timer."""
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)
    await _turn_on(hass, **{ATTR_DURATION: 5})

    await hass.services.async_call(
        SIREN_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: BUZZER_ENTITY_ID},
        blocking=True,
    )

    mock_client.buzz_stop.assert_awaited_once_with(17)
    assert hass.states.get(BUZZER_ENTITY_ID).state == "off"


@pytest.mark.usefixtures("siren_only")
async def test_unload_stops_a_sounding_buzzer(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An unload while the buzzer sounds sends the stop, so nothing is left buzzing."""
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)
    await _turn_on(hass)

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_client.buzz_stop.assert_awaited_once_with(17)


@pytest.mark.usefixtures("siren_only")
async def test_buzzer_follows_the_connection(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The siren reads unavailable while the broker connection is down, and back."""
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)

    mock_client.available = False
    emit(mock_client, AvailabilityChanged(available=False))
    await hass.async_block_till_done()
    assert hass.states.get(BUZZER_ENTITY_ID).state == STATE_UNAVAILABLE

    mock_client.available = True
    emit(mock_client, AvailabilityChanged(available=True))
    await hass.async_block_till_done()
    assert hass.states.get(BUZZER_ENTITY_ID).state != STATE_UNAVAILABLE


@pytest.mark.usefixtures("siren_only")
async def test_withheld_enumeration_names_every_row(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A standard account cannot read capabilities, so it names every row.

    The enumeration is what tells a withheld record apart from one Ampio
    Designer dropped. A bare capability check would name none of them, and
    an orphaned buzzer record would land in the wrong repair card.
    """
    mock_client.access_tier = AccessTier.RESTRICTED

    await setup_integration(hass, mock_config_entry)

    withheld = mock_config_entry.runtime_data.withheld_unique_ids()
    assert "module_17_buzzer" in withheld


@pytest.mark.usefixtures("siren_only")
async def test_buzz_pattern_passes_the_frame_fields(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Three pips are one tone, one silent rest, three cycles, in one frame."""
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        DOMAIN,
        "buzz_pattern",
        {
            ATTR_ENTITY_ID: BUZZER_ENTITY_ID,
            "tone": 6,
            "seconds": 0.3,
            "tone2": 0,
            "seconds2": 0.3,
            "cycles": 3,
        },
        blocking=True,
    )

    mock_client.buzz_pattern.assert_awaited_once_with(
        17, tone=6, seconds=0.3, tone2=0, seconds2=0.3, cycles=3, delay=0.0
    )


@pytest.mark.usefixtures("siren_only")
@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"tone": 32, "seconds": 1}, id="tone-too-high"),
        pytest.param({"tone": 6, "seconds": 1000}, id="seconds-too-long"),
        pytest.param({"tone": 6, "seconds": 1, "cycles": 255}, id="cycles-too-many"),
        pytest.param({"tone": 6, "seconds": 1, "tone2": 32}, id="tone2-too-high"),
        pytest.param(
            {"tone": 6, "seconds": 1, "seconds2": 1000}, id="seconds2-too-long"
        ),
        pytest.param({"tone": 6, "seconds": 1, "delay": 1000}, id="delay-too-long"),
    ],
)
async def test_buzz_pattern_rejects_a_value_off_the_wire(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    data: dict[str, object],
) -> None:
    """The schema holds each field to the range the frame accepts.

    Each case leaves every other field valid, so a case fails only when the
    range on its own named field is gone, not when some other field's range
    happens to catch the same out-of-bounds request.
    """
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "buzz_pattern",
            {ATTR_ENTITY_ID: BUZZER_ENTITY_ID, **data},
            blocking=True,
        )
    mock_client.buzz_pattern.assert_not_awaited()


@pytest.mark.usefixtures("siren_only")
async def test_buzz_pattern_defaults_cycles_to_one(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Omitting ``cycles`` sends one pass, not the latch that zero asks for."""
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        DOMAIN,
        "buzz_pattern",
        {ATTR_ENTITY_ID: BUZZER_ENTITY_ID, "tone": 6, "seconds": 0.3},
        blocking=True,
    )

    assert mock_client.buzz_pattern.await_args.kwargs["cycles"] == 1


@pytest.mark.usefixtures("siren_only")
async def test_buzz_pattern_finite_cycles_clears_state_when_the_run_elapses(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The state clears once delay plus every cycle's two steps has elapsed, not before.

    The bracket around the run's total is narrow, close enough that a wrong
    formula moves the expiry outside it, not only an absent timer.
    """
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        DOMAIN,
        "buzz_pattern",
        {
            ATTR_ENTITY_ID: BUZZER_ENTITY_ID,
            "tone": 6,
            "seconds": 0.4,
            "tone2": 6,
            "seconds2": 0.6,
            "cycles": 3,
            "delay": 0.5,
        },
        blocking=True,
    )
    assert hass.states.get(BUZZER_ENTITY_ID).state == "on"

    # The run is delay + cycles * (seconds + seconds2) = 0.5 + 3 * 1 = 3.5 s.
    # async_fire_time_changed adds its own fixed 0.5 s, so the elapsed
    # argument sits 0.5 s under the simulated instant it produces.
    await _elapse(hass, 2.9)
    assert hass.states.get(BUZZER_ENTITY_ID).state == "on"

    await _elapse(hass, 3.1)
    assert hass.states.get(BUZZER_ENTITY_ID).state == "off"


@pytest.mark.usefixtures("siren_only")
async def test_buzz_pattern_cycles_zero_leaves_it_on_with_no_timer(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """``cycles`` 0 repeats until a stop, so no timer ever clears the state."""
    with_buzzer(mock_client)
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        DOMAIN,
        "buzz_pattern",
        {ATTR_ENTITY_ID: BUZZER_ENTITY_ID, "tone": 6, "seconds": 0.3, "cycles": 0},
        blocking=True,
    )
    assert hass.states.get(BUZZER_ENTITY_ID).state == "on"

    await _elapse(hass, 100000)
    assert hass.states.get(BUZZER_ENTITY_ID).state == "on"
