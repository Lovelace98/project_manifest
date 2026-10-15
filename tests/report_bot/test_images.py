from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from report_bot.images import clear_near_white_background, transparent_logo_png


def test_clear_near_white_background_removes_the_header_box(
    project_root: Path,
    assert_no_opaque_near_white,
) -> None:
    with Image.open(project_root / "blueprint_logo.png") as source:
        cleaned = clear_near_white_background(source)
        assert cleaned.mode == "RGBA"
        assert_no_opaque_near_white(cleaned)
        alpha = cleaned.getchannel("A")
        assert alpha.getextrema() == (0, 255)
        opaque = alpha.point([0] * 1 + [255] * 255)
        assert opaque.histogram()[255] > 50_000


def test_clear_near_white_background_keeps_transparent_footer_intact(
    project_root: Path,
) -> None:
    footer = project_root / "flow" / "static" / "assets" / "Contact PNG.png"
    with Image.open(footer) as source:
        before = source.convert("RGBA")
        cleaned = clear_near_white_background(source)
        assert list(cleaned.getdata()) == list(before.getdata())


def test_transparent_logo_png_returns_cleaned_cached_bytes(
    project_root: Path,
    assert_no_opaque_near_white,
) -> None:
    logo_path = project_root / "blueprint_logo.png"
    first = transparent_logo_png(logo_path)
    assert transparent_logo_png(logo_path) is first
    with Image.open(io.BytesIO(first)) as image:
        assert_no_opaque_near_white(image)
