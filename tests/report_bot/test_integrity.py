from __future__ import annotations

import re
import pymupdf as fitz
import pytest

from report_bot.domain import CaseType, LabSource, Participant
from report_bot.integrity import (
    cross_verify_participants,
    detect_layout_anomalies,
    determine_report_classification,
)


def test_detect_layout_anomalies_corrupted():
    warnings = detect_layout_anomalies(
        b"not-a-valid-pdf",
        lab_source=LabSource.EUROFINS,
        case_type=CaseType.PERSONAL,
    )
    assert len(warnings) == 1
    assert "Unable to inspect PDF structure" in warnings[0]


def test_detect_layout_anomalies_shifted_eurofins():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Insert title at bottom of page (y=700) instead of normal header range (70-260)
    page.insert_text((50, 700), "TEST CERTIFICATE", fontsize=14)
    page.insert_text((50, 150), "SAMPLE", fontsize=10)
    pdf_bytes = doc.tobytes()
    doc.close()

    warnings = detect_layout_anomalies(
        pdf_bytes,
        lab_source=LabSource.EUROFINS,
        case_type=CaseType.PERSONAL,
    )
    assert any("Layout Shift: Title anchor is at unexpected y-coordinate" in w for w in warnings)


def test_detect_layout_anomalies_ufc_no_chain_of_custody_conflict():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 50), "DNA Test Report\nNo Chain of Custody\nOrder ID: CD-ACC-120426-01\nUniversal Forensics", fontsize=10)
    pdf_bytes = doc.tobytes()
    doc.close()

    warnings = detect_layout_anomalies(
        pdf_bytes,
        lab_source=LabSource.UFC,
        case_type=CaseType.CERTIFIED_LEGAL,
    )
    assert any("No Chain of Custody" in w for w in warnings)


def test_determine_report_classification_personal_dual_and_with_additional_tags():
    # Notion has multiple tags like 'Additional party', 'Self-Collection Kit', 'Discreet'
    # but lacks 'Legal' -> Clean Personal
    res = determine_report_classification(
        "PP-KEN-120425-01",
        notion_data={"found": True, "test_or_service": ["Paternity", "Self-Collection Kit", "Additional party"]},
    )
    assert res["case_type"] == CaseType.PERSONAL
    assert res["verified"] is True
    assert res["mismatch"] is False
    assert res["needs_manual_review"] is False
    assert res["badge_status"] == "success"


def test_determine_report_classification_certified_dual_and_with_additional_tags():
    # Notion has 'Legal' along with other tags like 'Y-Chromosome' and 'Additional parties'
    # -> Clean Certified / Legal
    res = determine_report_classification(
        "CD-ACC-120425-01",
        notion_data={"found": True, "test_or_service": ["Y-Chromosome", "Legal", "Additional parties"]},
    )
    assert res["case_type"] == CaseType.CERTIFIED_LEGAL
    assert res["verified"] is True
    assert res["mismatch"] is False
    assert res["needs_manual_review"] is False
    assert res["badge_status"] == "success"


def test_determine_report_classification_mismatch_pp_with_legal():
    # Personal prefix PP + Notion CONTAINS Legal -> Discrepancy flag
    res = determine_report_classification(
        "PP-ACC-120425-01",
        notion_data={"found": True, "test_or_service": ["Paternity", "Legal", "Additional party"]},
    )
    assert res["mismatch"] is True
    assert res["needs_manual_review"] is True
    assert "Classification Mismatch" in res["reason"]
    assert res["badge_status"] == "warning"


def test_determine_report_classification_mismatch_cd_without_legal():
    # CD prefix + Notion LACKS Legal -> Discrepancy flag
    res = determine_report_classification(
        "CD-ACC-120425-01",
        notion_data={"found": True, "test_or_service": ["Paternity", "Additional party"]},
    )
    assert res["mismatch"] is True
    assert res["needs_manual_review"] is True
    assert "Classification Mismatch" in res["reason"]
    assert res["badge_status"] == "warning"


def test_determine_report_classification_unrecognized_prefix_with_notion():
    # Unrecognized prefix e.g. G34_0408-1 from UFC filename/ID, but Notion has record
    res_legal = determine_report_classification(
        "G34_0408-1",
        notion_data={"found": True, "test_or_service": ["Paternity", "Legal"]},
    )
    assert res_legal["case_type"] == CaseType.CERTIFIED_LEGAL
    assert res_legal["needs_manual_review"] is True

    res_pers = determine_report_classification(
        "G34_0408-1",
        notion_data={"found": True, "test_or_service": ["Paternity", "Additional party"]},
    )
    assert res_pers["case_type"] == CaseType.PERSONAL
    assert res_pers["needs_manual_review"] is True


def test_determine_report_classification_notion_offline_fallbacks():
    # Prefix PP with no Notion data -> PERSONAL fallback without failure
    res_pp = determine_report_classification("PP-KEN-120425-01", notion_data=None)
    assert res_pp["case_type"] == CaseType.PERSONAL
    assert res_pp["needs_manual_review"] is False

    # Prefix CD with no Notion data -> CERTIFIED_LEGAL fallback without failure
    res_cd = determine_report_classification("CD-KEN-120425-01", notion_data=None)
    assert res_cd["case_type"] == CaseType.CERTIFIED_LEGAL
    assert res_cd["needs_manual_review"] is False

    # Unknown prefix with no Notion data -> Needs manual review
    res_unknown = determine_report_classification("UNKNOWN-12345", notion_data=None)
    assert res_unknown["needs_manual_review"] is True


def test_cross_verify_participants_match():
    extracted = [
        Participant(
            id="1",
            sample_number="S1",
            role="ALLEGED FATHER",
            full_name="JOHN DOE",
            date_of_birth="15/05/1985",
            source_order=1,
        ),
        Participant(
            id="2",
            sample_number="S2",
            role="CHILD",
            full_name="BABY DOE",
            date_of_birth="20/10/2020",
            source_order=2,
        ),
    ]
    notion_parts = [
        {"name": "JOHN DOE", "role": "ALLEGED FATHER", "dob": "15/05/1985"},
        {"name": "BABY DOE", "role": "CHILD", "dob": "20/10/2020"},
    ]

    res = cross_verify_participants(extracted, notion_parts)
    assert res["verified"] is True
    assert len(res["discrepancies"]) == 0
    assert len(res["comparison"]) == 2
    assert all(c["status"] == "MATCHED" for c in res["comparison"])


def test_cross_verify_participants_discrepancy():
    extracted = [
        Participant(
            id="1",
            sample_number="S1",
            role="ALLEGED FATHER",
            full_name="JOHN DOE",
            date_of_birth="15/05/1985",
            source_order=1,
        )
    ]
    notion_parts = [
        {"name": "JOHN DOE", "role": "MOTHER", "dob": "01/01/1990"}
    ]

    res = cross_verify_participants(extracted, notion_parts)
    assert res["verified"] is False
    assert len(res["discrepancies"]) >= 2
    assert any("DOB discrepancy" in d for d in res["discrepancies"])
    assert any("Role discrepancy" in d for d in res["discrepancies"])


def test_detect_layout_anomalies_eurofins_multipage_clean():
    doc = fitz.open()
    # Page 1: Header + Sample table + Results heading
    page1 = doc.new_page(width=595, height=842)
    page1.insert_text((50, 100), "TEST CERTIFICATE", fontsize=14)
    page1.insert_text((50, 200), "SAMPLE", fontsize=10)
    page1.insert_text((50, 300), "Results:", fontsize=10)
    # Page 2: Markers table, signatures, and footer disclaimer
    page2 = doc.new_page(width=595, height=842)
    page2.insert_text((50, 780), "This laboratory carries out all analyses in accordance with standards.", fontsize=8)
    pdf_bytes = doc.tobytes()
    doc.close()

    warnings = detect_layout_anomalies(
        pdf_bytes,
        lab_source=LabSource.EUROFINS,
        case_type=CaseType.PERSONAL,
    )
    # Should have ZERO layout warnings because page 2 has the disclaimer anchor
    assert warnings == []


def test_detect_layout_anomalies_eurofins_multipage_truly_missing_disclaimer():
    doc = fitz.open()
    page1 = doc.new_page(width=595, height=842)
    page1.insert_text((50, 100), "TEST CERTIFICATE", fontsize=14)
    page1.insert_text((50, 200), "SAMPLE", fontsize=10)
    page1.insert_text((50, 300), "Results:", fontsize=10)
    page2 = doc.new_page(width=595, height=842)
    page2.insert_text((50, 780), "Random text without the standard disclaimer anchor.", fontsize=8)
    pdf_bytes = doc.tobytes()
    doc.close()

    warnings = detect_layout_anomalies(
        pdf_bytes,
        lab_source=LabSource.EUROFINS,
        case_type=CaseType.PERSONAL,
    )
    assert any("Eurofins footer disclaimer anchor missing" in w for w in warnings)


def test_determine_report_classification_defaults_to_issue():
    res = determine_report_classification(
        case_code="XYZ-ACC-101026-01",
        notion_data={"found": False},
        default_case_type=CaseType.ISSUE,
    )
    assert res["case_type"] == CaseType.ISSUE
    assert res["badge_label"] == "Issue (Unclassified)"
    assert res["badge_status"] == "danger"
    assert res["needs_manual_review"] is True
    assert "select Personal or Certified / Legal before approval" in res["reason"]


def test_determine_report_classification_fallback_personal():
    res = determine_report_classification(
        case_code="XYZ-ACC-101026-01",
        notion_data={"found": False},
        default_case_type=CaseType.PERSONAL,
    )
    assert res["case_type"] == CaseType.PERSONAL
    assert res["badge_status"] == "warning"


def test_ufc_dob_auto_enrichment():
    extracted = [
        Participant(
            id="1",
            sample_number="S1",
            role="ALLEGED FATHER",
            full_name="KWAME MENSAH",
            date_of_birth="-",
            source_order=1,
        ),
        Participant(
            id="2",
            sample_number="S2",
            role="CHILD",
            full_name="KOFI MENSAH",
            date_of_birth="-",
            source_order=2,
        ),
    ]
    notion_parts = [
        {"name": "KWAME MENSAH", "role": "ALLEGED FATHER", "dob": "12/05/1980"},
        {"name": "KOFI MENSAH", "role": "CHILD", "dob": "25/11/2018"},
    ]

    for ext in extracted:
        if not ext.date_of_birth or ext.date_of_birth == "-":
            norm_ext = re.sub(r"[^A-Z]", "", ext.full_name.upper())
            for n_p in notion_parts:
                norm_notion = re.sub(r"[^A-Z]", "", str(n_p.get("name", "")).upper())
                if norm_ext and norm_ext == norm_notion:
                    ext.date_of_birth = n_p.get("dob")
                    break

    assert extracted[0].date_of_birth == "12/05/1980"
    assert extracted[1].date_of_birth == "25/11/2018"

    res = cross_verify_participants(extracted, notion_parts)
    assert res["verified"] is True
    assert len(res["discrepancies"]) == 0



