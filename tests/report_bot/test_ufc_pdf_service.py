from pathlib import Path

import pymupdf as fitz

from report_bot.domain import CaseType
from report_bot.pdf_service import (
    PERSONAL_WARNING,
    rebrand_ufc_pdf,
)

BASE_DIR = Path(__file__).resolve().parent.parent
LOGO_PATH = BASE_DIR / "blueprint_logo.png"


def test_rebrand_ufc_ychromosome_certified_legal():
    source_path = BASE_DIR / "CD G34_0412-2.pdf"
    if not source_path.exists():
        import pytest
        pytest.skip("CD G34_0412-2.pdf not found")
    source_bytes = source_path.read_bytes()
    rebranded_bytes = rebrand_ufc_pdf(
        source_bytes,
        case_type=CaseType.CERTIFIED_LEGAL,
        test_type="Y-CHROMOSOME",
        logo_path=LOGO_PATH,
    )

    doc = fitz.open(stream=rebranded_bytes, filetype="pdf")
    page = doc[0]

    # In Certified/Legal, personal warning should NOT be present
    assert not page.search_for(PERSONAL_WARNING)
    # UFC footer should be gone
    assert not page.search_for("Testing performed by Universal Forensics")
    # Blueprint footer should be present
    assert page.search_for("info@blueprintdna.org")

    # In Certified/Legal, "No Chain of Custody" must have "No" redacted, leaving "Chain of Custody"
    assert not page.search_for("No Chain of Custody")
    assert page.search_for("Chain of Custody")

    # Check words around the Chain of Custody area to ensure "No" is absent
    words = [w[4] for w in page.get_text("words") if 50 <= w[1] <= 75 and w[0] > 450]
    assert "No" not in words
    assert "Chain" in words
    assert "Custody" in words

    # Scientific conclusion and marker table intact
    assert page.search_for("Y-chromosome profiles that do not match")
    assert page.search_for("DYS456")


def test_rebrand_ufc_ychromosome_personal():
    source_path = BASE_DIR / "CD G34_0412-2.pdf"
    if not source_path.exists():
        import pytest
        pytest.skip("CD G34_0412-2.pdf not found")
    source_bytes = source_path.read_bytes()
    rebranded_bytes = rebrand_ufc_pdf(
        source_bytes,
        case_type=CaseType.PERSONAL,
        test_type="Y-CHROMOSOME",
        logo_path=LOGO_PATH,
    )

    doc = fitz.open(stream=rebranded_bytes, filetype="pdf")
    page = doc[0]

    # Personal warning must be present
    assert page.search_for(PERSONAL_WARNING)
    # UFC footer should be gone
    assert not page.search_for("Testing performed by Universal Forensics")
    # Blueprint footer should be present
    assert page.search_for("info@blueprintdna.org")
    # In Personal, "No Chain of Custody" remains intact
    assert page.search_for("No Chain of Custody")


def test_rebrand_ufc_personal():
    source_path = BASE_DIR / "G34_0408-1.pdf"
    if not source_path.exists():
        import pytest
        pytest.skip("G34_0408-1.pdf not found")
    source_bytes = source_path.read_bytes()
    rebranded_bytes = rebrand_ufc_pdf(
        source_bytes,
        case_type=CaseType.PERSONAL,
        test_type="PATERNITY",
        logo_path=LOGO_PATH,
    )

    doc = fitz.open(stream=rebranded_bytes, filetype="pdf")
    page = doc[0]

    assert not page.search_for("Testing performed by Universal Forensics")
    assert page.search_for("info@blueprintdna.org")
    assert page.search_for(PERSONAL_WARNING)
    assert page.search_for("No Chain of Custody")
    assert len(page.get_images()) > 0


def test_rebrand_ufc_certified_legal():
    source_path = BASE_DIR / "G34_0408-1.pdf"
    if not source_path.exists():
        import pytest
        pytest.skip("G34_0408-1.pdf not found")
    source_bytes = source_path.read_bytes()
    rebranded_bytes = rebrand_ufc_pdf(
        source_bytes,
        case_type=CaseType.CERTIFIED_LEGAL,
        test_type="PATERNITY",
        logo_path=LOGO_PATH,
    )

    doc = fitz.open(stream=rebranded_bytes, filetype="pdf")
    page = doc[0]

    assert not page.search_for(PERSONAL_WARNING)
    assert not page.search_for("Testing performed by Universal Forensics")
    assert page.search_for("info@blueprintdna.org")
    assert not page.search_for("No Chain of Custody")
    assert page.search_for("Chain of Custody")


def test_rebrand_ufc_siblingship_legal():
    source_path = BASE_DIR / "G34_0403-2.pdf"
    if not source_path.exists():
        import pytest
        pytest.skip("G34_0403-2.pdf not found")
    source_bytes = source_path.read_bytes()
    rebranded_bytes = rebrand_ufc_pdf(
        source_bytes,
        case_type=CaseType.CERTIFIED_LEGAL,
        test_type="SIBLINGSHIP",
        logo_path=LOGO_PATH,
    )

    doc = fitz.open(stream=rebranded_bytes, filetype="pdf")
    page = doc[0]

    assert not page.search_for("No Chain of Custody")
    assert page.search_for("Chain of Custody")
    assert not page.search_for(PERSONAL_WARNING)
    assert not page.search_for("Testing performed by Universal Forensics")
    assert page.search_for("info@blueprintdna.org")
