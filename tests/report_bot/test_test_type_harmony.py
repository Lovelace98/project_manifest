from __future__ import annotations

import io
from pathlib import Path

import pymupdf as fitz
import pytest

from report_bot.domain import CaseType, Participant
from report_bot.integrity import determine_test_type, normalize_test_type
from report_bot.parser import parse_eurofins_report


def create_tight_line_spacing_pdf() -> bytes:
    """Creates a PDF where the header and first row bounding boxes touch/overlap slightly."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(fitz.Point(70, 140), "21/10/2026")
    page.insert_text(fitz.Point(70, 160), "Personal Paternity Test Certificate", fontsize=14)
    page.insert_text(fitz.Point(70, 180), "Case reference number: #CD-FML-121026-01§")
    page.insert_text(fitz.Point(70, 195), "SAMPLE NR.")
    page.insert_text(fitz.Point(180, 195), "ROLE")
    page.insert_text(fitz.Point(264, 195), "NAME")
    page.insert_text(fitz.Point(422, 195), "DATE OF BIRTH")
    # Row 1 (Alleged Father)
    page.insert_text(fitz.Point(70, 207), "VAT261019_088")
    page.insert_text(fitz.Point(180, 207), "Alleged Father")
    page.insert_text(fitz.Point(264, 207), "JOSEPH BOATENG")
    page.insert_text(fitz.Point(422, 207), "11/04/1991")
    # Row 2 (Child)
    page.insert_text(fitz.Point(70, 220), "VAT261019_089")
    page.insert_text(fitz.Point(180, 220), "Child")
    page.insert_text(fitz.Point(264, 220), "IVAN PAA KWASI BOATENG")
    page.insert_text(fitz.Point(422, 220), "19/03/2017")
    page.insert_text(fitz.Point(70, 245), "DNA isolation was carried out separately for all samples.")
    page.insert_text(
        fitz.Point(70, 600),
        "Conclusions: JOSEPH BOATENG is excluded from being the biological father of IVAN PAA KWASI BOATENG.",
    )
    return doc.tobytes()


def create_multi_child_personal_certificate_pdf() -> bytes:
    """Creates a PDF with title 'Personal Test Certificate' but with 1 father and 3 children."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(fitz.Point(70, 140), "21/10/2026")
    page.insert_text(fitz.Point(70, 160), "Personal Test Certificate", fontsize=14)
    page.insert_text(fitz.Point(70, 180), "Case reference number: #CD-KBDO-071026-01-R§")
    page.insert_text(fitz.Point(70, 195), "SAMPLE NR.")
    page.insert_text(fitz.Point(180, 195), "ROLE")
    page.insert_text(fitz.Point(264, 195), "NAME")
    page.insert_text(fitz.Point(422, 195), "DATE OF BIRTH")
    # Father
    page.insert_text(fitz.Point(70, 210), "HID261012_055")
    page.insert_text(fitz.Point(180, 210), "Alleged Father")
    page.insert_text(fitz.Point(264, 210), "PHILIP KWABENA ARTHUR")
    page.insert_text(fitz.Point(422, 210), "01/07/1978")
    # Children
    page.insert_text(fitz.Point(70, 225), "HID261012_056")
    page.insert_text(fitz.Point(180, 225), "Child")
    page.insert_text(fitz.Point(264, 225), "ELSIE ARTHUR")
    page.insert_text(fitz.Point(422, 225), "11/11/2012")
    page.insert_text(fitz.Point(70, 240), "HID261012_057")
    page.insert_text(fitz.Point(180, 240), "Child")
    page.insert_text(fitz.Point(264, 240), "PHILIPA ARTHUR")
    page.insert_text(fitz.Point(422, 240), "05/10/2016")
    page.insert_text(fitz.Point(70, 255), "HID261012_058")
    page.insert_text(fitz.Point(180, 255), "Child")
    page.insert_text(fitz.Point(264, 255), "GABRIELLA NANA YAA GYIMAH")
    page.insert_text(fitz.Point(422, 255), "11/03/2021")
    page.insert_text(fitz.Point(70, 280), "DNA isolation was carried out separately for all samples.")
    page.insert_text(
        fitz.Point(70, 600),
        "Conclusions: The probability of PHILIP KWABENA ARTHUR being the biological father of ELSIE ARTHUR is 99.9993 %.",
    )
    return doc.tobytes()


def test_tight_line_spacing_parses_all_participants() -> None:
    pdf_bytes = create_tight_line_spacing_pdf()
    report = parse_eurofins_report(pdf_bytes, "CD-FML-121026-01.pdf")
    assert report.case_code == "CD-FML-121026-01"
    assert len(report.participants) == 2
    assert report.participants[0].sample_number == "VAT261019_088"
    assert report.participants[0].role == "ALLEGED FATHER"
    assert report.participants[0].full_name == "JOSEPH BOATENG"
    assert report.participants[1].sample_number == "VAT261019_089"
    assert report.participants[1].role == "CHILD"
    assert report.test_type == "PATERNITY"


def test_multi_participant_personal_test_is_not_single_profile() -> None:
    pdf_bytes = create_multi_child_personal_certificate_pdf()
    report = parse_eurofins_report(pdf_bytes, "CD-KBDO-071026-01-R.pdf")
    assert report.case_code == "CD-KBDO-071026-01"
    assert len(report.participants) == 4
    assert report.participants[0].role == "ALLEGED FATHER"
    assert report.test_type == "PATERNITY"
    assert report.test_type != "SINGLE PROFILE"


def test_single_profile_guardrail_in_determine_test_type() -> None:
    participants = [
        Participant(sample_number="S1", role="ALLEGED FATHER", full_name="JOHN DOE", date_of_birth="01/01/1980", source_order=0),
        Participant(sample_number="S2", role="CHILD", full_name="JANE DOE", date_of_birth="01/01/2010", source_order=1),
    ]
    # Even if current_test_type claims SINGLE PROFILE, guardrail auto-corrects for 2 participants
    res = determine_test_type("SINGLE PROFILE", participants, None)
    assert res["test_type"] == "PATERNITY"
    assert "Auto-corrected Single Profile" in res.get("reason", "")


def test_notion_harmony_enriches_test_type() -> None:
    participants = [
        Participant(sample_number="S1", role="ALLEGED FATHER", full_name="JOHN DOE", date_of_birth="01/01/1980", source_order=0),
        Participant(sample_number="S2", role="CHILD", full_name="JANE DOE", date_of_birth="01/01/2010", source_order=1),
    ]
    notion_data = {
        "found": True,
        "manifest_test_type": "PRIVATE PATERNITY",
        "test_or_service": ["Paternity", "Legal", "Additional parties"],
    }
    res = determine_test_type("PATERNITY", participants, notion_data)
    assert res["test_type"] == "PATERNITY"
    assert res["verified"] is True
    assert res["needs_manual_review"] is False


def test_notion_discrepancy_triggers_manual_review() -> None:
    participants = [
        Participant(sample_number="S1", role="ALLEGED FATHER", full_name="JOHN DOE", date_of_birth="01/01/1980", source_order=0),
        Participant(sample_number="S2", role="CHILD", full_name="JANE DOE", date_of_birth="01/01/2010", source_order=1),
    ]
    # Notion erroneously lists MATERNITY for an Alleged Father case
    notion_data = {
        "found": True,
        "manifest_test_type": "PRIVATE MATERNITY",
        "test_or_service": ["Maternity"],
    }
    res = determine_test_type("PATERNITY", participants, notion_data)
    assert res["needs_manual_review"] is True
    assert res["verified"] is False
    assert "Test Type Mismatch" in res.get("reason", "")


def test_normalize_test_type() -> None:
    assert normalize_test_type("LEGAL PATERNITY") == "PATERNITY"
    assert normalize_test_type("PRIVATE PATERNITY + MATERNITY") == "PATERNITY + MATERNITY"
    assert normalize_test_type("LEGAL Y CHROMOSOME TEST") == "Y-CHROMOSOME"
    assert normalize_test_type("LEGAL SINGLE PROFILE") == "SINGLE PROFILE"
    assert normalize_test_type("PERSONAL GRANDPARENTAGE TEST") == "GRANDPARENTAGE"
