"""Tests for the brand images shipped inside the integration."""

from http import HTTPStatus
import struct

import pytest
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.ampio.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_custom_components
from homeassistant.setup import async_setup_component

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_size(data: bytes) -> tuple[int, int]:
    """Return the width and height recorded in a PNG header."""
    assert data[:8] == PNG_SIGNATURE
    width, height = struct.unpack(">II", data[16:24])
    return width, height


async def test_loader_sees_brand_folder(hass: HomeAssistant) -> None:
    """The loader flags the integration as branded."""
    integrations = await async_get_custom_components(hass)
    assert integrations[DOMAIN].has_branding


@pytest.mark.parametrize(
    ("image", "width", "height"),
    [
        ("icon.png", 256, 256),
        ("icon@2x.png", 512, 512),
        ("logo.png", None, 256),
        ("logo@2x.png", None, 512),
    ],
)
async def test_brand_image_served_from_folder(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    image: str,
    width: int | None,
    height: int,
) -> None:
    """The brands view serves the local file, sized per the brands rules."""
    integration = (await async_get_custom_components(hass))[DOMAIN]
    expected = (integration.file_path / "brand" / image).read_bytes()
    actual_width, actual_height = png_size(expected)
    assert actual_height == height
    assert width is None or actual_width == width

    assert await async_setup_component(hass, "brands", {})
    client = await hass_client()
    response = await client.get(f"/api/brands/integration/{DOMAIN}/{image}")

    assert response.status == HTTPStatus.OK
    assert response.content_type == "image/png"
    assert await response.read() == expected
