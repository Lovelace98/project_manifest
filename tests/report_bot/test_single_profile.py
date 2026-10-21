from __future__ import annotations

from datetime import date
from pathlib import Path

import pymupdf as fitz
import pytest

from report_bot.domain import CaseType, Participant
from report_bot.integrity import cross_verify_participants, determine_report_classification
from report_bot.parser import parse_eurofins_report
from report_bot.pdf_service import rebrand_pdf
from report_bot.verification import build_verification_docx, LibreOfficeConverter

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "CD-BDO-141026-03.pdf"


def get_single_profile_pdf_bytes() -> bytes:
    if FIXTURE_PATH.exists():
        return FIXTURE_PATH.read_bytes()

    # Generate synthetic Eurofins Single Profile PDF matching the exact structure
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(fitz.Point(70, 140), "20/10/2026")
    page.insert_text(fitz.Point(70, 160), "Personal Test Certificate", fontsize=14)
    page.insert_text(fitz.Point(70, 180), "Case reference number: #CD-BDO-141026-03§")
    page.insert_text(fitz.Point(70, 194), "SAMPLE NR.")
    page.insert_text(fitz.Point(180, 194), "NAME")
    page.insert_text(fitz.Point(390, 194), "DATE OF BIRTH")
    page.insert_text(fitz.Point(70, 214), "HID261019_102")
    page.insert_text(fitz.Point(180, 214), "BOADUWAA AGYEMANG DONKOR")
    page.insert_text(fitz.Point(390, 214), "17/06/2019")
    page.insert_text(fitz.Point(70, 228), "HID261019_103")
    page.insert_text(fitz.Point(180, 228), "BOADUWAA AGYEMANG DONKOR")
    page.insert_text(fitz.Point(390, 228), "17/06/2019")
    page.insert_text(fitz.Point(70, 255), "DNA isolation was carried out separately for all samples.")
    page.insert_text(fitz.Point(70, 700), "Lorena Krickl, M. Sc.")
    page.insert_text(fitz.Point(70, 750), "Eurofins Medigenomix Forensik GmbH carries out all analyses with greatest care.")
    return doc.tobytes()


def test_single_profile_eurofins_report_parsing() -> None:
    pdf_bytes = get_single_profile_pdf_bytes()

    parsed = parse_eurofins_report(pdf_bytes, "CD-BDO-141026-03.pdf")

    assert parsed.case_code == "CD-BDO-141026-03"
    assert parsed.test_type == "SINGLE PROFILE"
    assert parsed.collection_date == date(2026, 10, 14)
    assert not parsed.blocking_errors

    # Verify duplicate swab rows for same person are collapsed to 1 participant
    assert len(parsed.participants) == 1
    p = parsed.participants[0]
    assert p.full_name == "BOADUWAA AGYEMANG DONKOR"
    assert p.date_of_birth == "17/06/2019"
    assert p.role == "PARTICIPANT"
    assert p.sample_number == "HID261019_102, HID261019_103"


def test_single_profile_notion_cross_verification() -> None:
    pdf_bytes = get_single_profile_pdf_bytes()
    parsed = parse_eurofins_report(pdf_bytes, "CD-BDO-141026-03.pdf")

    notion_participants = [
        {"name": "BOADUWAA AGYEMANG DONKOR", "dob": "17062019", "role": "CHILD"}
    ]

    res = cross_verify_participants(parsed.participants, notion_participants)
    assert res["verified"] is True
    assert res["discrepancies"] == []
    assert len(res["comparison"]) == 1
    assert res["comparison"][0]["status"] == "MATCHED"


def test_single_profile_classification() -> None:
    notion_data = {
        "found": True,
        "test_or_service": ["Single Profile", "Legal"],
    }
    classification = determine_report_classification("CD-BDO-141026-03", notion_data)
    assert classification["case_type"] == CaseType.CERTIFIED_LEGAL
    assert classification["verified"] is True
    assert classification["needs_manual_review"] is False


def test_single_profile_rebranding(project_root: Path) -> None:
    pdf_bytes = get_single_profile_pdf_bytes()
    logo_path = project_root / "blueprint_logo.png"

    rebranded_bytes = rebrand_pdf(
        pdf_bytes,
        case_type=CaseType.CERTIFIED_LEGAL,
        test_type="SINGLE PROFILE",
        logo_path=logo_path,
    )
    doc = fitz.open(stream=rebranded_bytes, filetype="pdf")
    try:
        text = doc[0].get_text("text")
        assert "Legal Single Profile Test Certificate" in text
        assert "BOADUWAA AGYEMANG DONKOR" in text
    finally:
        doc.close()


def test_single_profile_verification_page_single_page(project_root: Path, tmp_path: Path) -> None:
    converter = LibreOfficeConverter()
    if not converter.available:
        pytest.skip("LibreOffice is not installed in the current environment")

    template = project_root / "verification page template.docx"
    out_docx = tmp_path / "verification.docx"
    out_pdf = tmp_path / "verification.pdf"

    participant = Participant(
        sample_number="HID261019_102, HID261019_103",
        role="PARTICIPANT",
        full_name="BOADUWAA AGYEMANG DONKOR",
        date_of_birth="17/06/2019",
        source_order=0,
    )

    build_verification_docx(
        template,
        out_docx,
        case_code="CD-BDO-141026-03",
        collection_date=date(2026, 10, 14),
        participants=[participant],
    )

    converter.convert(out_docx, out_pdf)

    doc = fitz.open(out_pdf)
    try:
        assert doc.page_count == 1
    finally:
        doc.close()
