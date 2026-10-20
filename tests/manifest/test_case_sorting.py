import os
import tempfile
from datetime import date
import openpyxl

from notion_service import (
    extract_case_date_and_seq,
    sort_manifest_cases,
    merge_multipart_cases,
)
from exporter import create_excel_manifest


def test_extract_case_date_and_seq_standard():
    # Standard format: CD-OMC-260526-001
    d, s, tag = extract_case_date_and_seq("CD-OMC-260526-001")
    assert d == date(2026, 5, 26)
    assert s == 1
    assert tag == "CD-OMC-260526-001"


def test_extract_case_date_and_seq_multisegment_prefix():
    # Multi-segment prefix: CD-BDO-HOM-031026-01
    d, s, tag = extract_case_date_and_seq("CD-BDO-HOM-031026-01")
    assert d == date(2026, 10, 3)
    assert s == 1
    assert tag == "CD-BDO-HOM-031026-01"


def test_extract_case_date_and_seq_motherlab_format():
    # Motherlab email format: X-101026-X and X-121026-X
    d1, s1, _ = extract_case_date_and_seq("X-101026-X")
    d2, s2, _ = extract_case_date_and_seq("X-121026-X")
    assert d1 == date(2026, 10, 10)
    assert d2 == date(2026, 10, 12)
    assert d1 < d2


def test_extract_case_date_and_seq_earlier_year():
    # 2025 case: BDO-HOM-291025-001
    d, s, tag = extract_case_date_and_seq("BDO-HOM-291025-001")
    assert d == date(2025, 10, 29)
    assert s == 1


def test_extract_case_date_and_seq_fallback_no_date():
    # No date in tag
    d, s, tag = extract_case_date_and_seq("PENDING-PL-58")
    assert d is None
    assert s == 58
    assert tag == "PENDING-PL-58"

    d2, s2, tag2 = extract_case_date_and_seq("")
    assert d2 is None
    assert s2 == 999999
    assert tag2 == ""


def test_sort_manifest_cases_chronological_ascending():
    cases = [
        {"bdo_tag": "X-121026-X"},
        {"bdo_tag": "CD-EBD-010626-001"},  # 1 June 2026
        {"bdo_tag": "CD-OMC-260526-001"},  # 26 May 2026 (earlier than June!)
        {"bdo_tag": "BDO-HOM-291025-001"},  # 29 Oct 2025 (earlier year!)
        {"bdo_tag": "X-101026-X"},          # 10 Oct 2026
        {"bdo_tag": "PENDING-PL-58"},       # No date (should be at end)
    ]

    sorted_cases = sort_manifest_cases(cases, ascending=True)
    tags = [c["bdo_tag"] for c in sorted_cases]

    expected = [
        "BDO-HOM-291025-001",  # 2025-10-29
        "CD-OMC-260526-001",   # 2026-05-26
        "CD-EBD-010626-001",   # 2026-06-01
        "X-101026-X",          # 2026-10-10
        "X-121026-X",          # 2026-10-12
        "PENDING-PL-58",       # No date
    ]
    assert tags == expected


def test_sort_manifest_cases_sequence_number_tiebreaker():
    cases = [
        {"bdo_tag": "CD-KBDO-300526-003"},
        {"bdo_tag": "CD-KBDO-300526-001"},
        {"bdo_tag": "CD-KBDO-300526-002"},
    ]

    sorted_cases = sort_manifest_cases(cases, ascending=True)
    tags = [c["bdo_tag"] for c in sorted_cases]

    assert tags == [
        "CD-KBDO-300526-001",
        "CD-KBDO-300526-002",
        "CD-KBDO-300526-003",
    ]


def test_sort_manifest_cases_descending_and_tag_sort():
    cases = [
        {"bdo_tag": "CD-OMC-260526-001"},
        {"bdo_tag": "X-121026-X"},
        {"bdo_tag": "X-101026-X"},
    ]

    desc = sort_manifest_cases(cases, ascending=False)
    assert [c["bdo_tag"] for c in desc] == [
        "X-121026-X",
        "X-101026-X",
        "CD-OMC-260526-001",
    ]

    by_tag = sort_manifest_cases(cases, ascending=True, sort_by="tag")
    assert [c["bdo_tag"] for c in by_tag] == [
        "CD-OMC-260526-001",
        "X-101026-X",
        "X-121026-X",
    ]


def test_merge_multipart_cases_automatically_sorts():
    row1 = {
        "page_id": "p1",
        "entry_id": "PL-1",
        "case_code": "CD-OMC-121026-01",  # 12 Oct 2026
        "manifest_test_type": "PRIVATE PATERNITY",
        "entry_part": "Part 1",
        "part_num": 1,
        "special_requests": "",
        "receiving_lab": "EUROFINS",
        "ready_for_manifest": True,
        "verified_reviewed": True,
        "case_status": "Manifest Awaiting",
        "case_page_ids": [],
        "participants": [{"name": "JOHN DOE", "dob": "01011990", "role": "ALLEGED FATHER", "ethnicity": "AFRICAN"}]
    }
    row2 = {
        "page_id": "p2",
        "entry_id": "PL-2",
        "case_code": "CD-OMC-101026-01",  # 10 Oct 2026 (earlier date!)
        "manifest_test_type": "PRIVATE PATERNITY",
        "entry_part": "Part 1",
        "part_num": 1,
        "special_requests": "",
        "receiving_lab": "EUROFINS",
        "ready_for_manifest": True,
        "verified_reviewed": True,
        "case_status": "Manifest Awaiting",
        "case_page_ids": [],
        "participants": [{"name": "JAMES SMITH", "dob": "02021992", "role": "ALLEGED FATHER", "ethnicity": "AFRICAN"}]
    }

    # Pass in row1 (12 Oct) before row2 (10 Oct)
    consolidated = merge_multipart_cases([row1, row2])

    assert len(consolidated) == 2
    # Should automatically sort in ascending order: 10 Oct before 12 Oct
    assert consolidated[0]["bdo_tag"] == "CD-OMC-101026-01"
    assert consolidated[1]["bdo_tag"] == "CD-OMC-121026-01"


def test_create_excel_manifest_preserves_sorted_sheets():
    cases = [
        {
            "bdo_tag": "CD-OMC-101026-01",
            "test_type": "PRIVATE PATERNITY",
            "special_requests": "None",
            "participants": [{"name": "JOHN", "dob": "01011990", "role": "ALLEGED FATHER", "ethnicity": "AFRICAN"}],
        },
        {
            "bdo_tag": "CD-OMC-121026-01",
            "test_type": "PRIVATE PATERNITY",
            "special_requests": "None",
            "participants": [{"name": "JAMES", "dob": "02021992", "role": "ALLEGED FATHER", "ethnicity": "AFRICAN"}],
        },
    ]

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        create_excel_manifest(cases, tmp_path)
        wb = openpyxl.load_workbook(tmp_path)
        assert wb.sheetnames == ["CD-OMC-101026-01", "CD-OMC-121026-01"]
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
