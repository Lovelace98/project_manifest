from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops

NEAR_WHITE_THRESHOLD = 250


def clear_near_white_background(image: Image.Image) -> Image.Image:
    """Return an RGBA copy whose near-white pixels are fully transparent."""
    rgba = image.convert("RGBA")
    red, green, blue, alpha = rgba.split()
    lightness = ImageChops.darker(ImageChops.darker(red, green), blue)
    near_white = lightness.point(
        [0] * NEAR_WHITE_THRESHOLD + [255] * (256 - NEAR_WHITE_THRESHOLD)
    )
    rgba.putalpha(ImageChops.darker(alpha, ImageChops.invert(near_white)))
    return rgba


@lru_cache(maxsize=8)
def transparent_logo_png(logo_path: Path) -> bytes:
    """Return the logo as PNG bytes with its near-white background removed."""
    with Image.open(logo_path) as image:
        image.load()
        output = io.BytesIO()
        clear_near_white_background(image).save(
            output,
            format="PNG",
            optimize=True,
            compress_level=9,
        )
        return output.getvalue()
