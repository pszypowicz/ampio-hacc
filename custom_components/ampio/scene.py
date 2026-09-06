"""Scene platform for the Ampio integration."""

from typing import Any

from ampio_mqtt import AmpioConnectionError, AmpioScene

from homeassistant.components.scene import Scene
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .data import AmpioConfigEntry, AmpioData
from .entity import HUB_IDENTIFIER

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AmpioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Ampio scenes from the app-defined scene catalogue.

    Scenes are not push objects: the catalogue is fetched once here. A
    fetch failure defers the platform so Home Assistant retries it.
    """
    data = entry.runtime_data
    try:
        scenes = await data.client.fetch_scenes()
    except AmpioConnectionError as err:
        raise PlatformNotReady(
            f"Fetching the Ampio scene catalogue failed: {err}"
        ) from err
    async_add_entities(
        AmpioSceneEntity(data, scene) for scene in scenes if scene.active
    )


class AmpioSceneEntity(Scene):
    """An activate-only scene backed by an Ampio app scene."""

    _attr_has_entity_name = True

    def __init__(self, data: AmpioData, scene: AmpioScene) -> None:
        """Initialize from the fetched catalogue entry.

        The scene id is the app's own identifier.
        """
        self._data = data
        self._scene_id = scene.id
        self._attr_name = scene.scene_name
        self._attr_unique_id = f"scene_{scene.id}"
        self._attr_device_info = DeviceInfo(identifiers={HUB_IDENTIFIER})

    async def async_activate(self, **kwargs: Any) -> None:
        """Apply the scene's actions."""
        await self._data.client.run_scene(self._scene_id)
