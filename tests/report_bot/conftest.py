from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageChops

from report_bot.config import Settings
from report_bot.images import NEAR_WHITE_THRESHOLD

ROOT = Path(__file__).resolve().parent.parent.parent
PDF_FIXTURES = sorted(ROOT.glob("CD-*.pdf"))


@pytest.fixture
def project_root() -> Path:
    return ROOT


@pytest.fixture
def pdf_fixtures() -> list[Path]:
    return PDF_FIXTURES


@pytest.fixture
def assert_no_opaque_near_white():
    def _assert(image: Image.Image) -> None:
        red, green, blue, alpha = image.convert("RGBA").split()
        lightness = ImageChops.darker(ImageChops.darker(red, green), blue)
        near_white = lightness.point(
            [0] * NEAR_WHITE_THRESHOLD + [255] * (256 - NEAR_WHITE_THRESHOLD)
        )
        opaque = alpha.point([0] * 1 + [255] * 255)
        assert ImageChops.multiply(near_white, opaque).getextrema() == (0, 0)

    return _assert


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        environment="development",
        dev_password="unit-test-password",
        session_secret="unit-test-session-secret-with-32-characters",
        pdf_owner_password="unit-test-owner-password",
        secure_cookies=False,
        cleanup_interval_seconds=60,
        data_dir=tmp_path / "data",
        job_dir=tmp_path / "data" / "jobs",
        settings_path=tmp_path / "data" / "settings.json",
        logo_path=ROOT / "blueprint_logo.png",
        contact_footer_path=ROOT / "flow" / "static" / "assets" / "Contact PNG.png",
        verification_template_path=ROOT / "verification page template.docx",
    )
