import pytest
from notion_service import (
    format_notion_dob,
    parse_part_number,
    merge_multipart_cases,
    process_raw_page,
    extract_prop_rollup_status
)

def test_format_notion_dob():
    assert format_notion_dob("2000-07-14") == "14072000"
    assert format_notion_dob("2026-01-26") == "26012026"
    assert format_notion_dob("1986-09-30") == "30091986"
    assert format_notion_dob("14/07/2000") == "14072000"
    assert format_notion_dob("14-07-2000") == "14072000"
    assert format_notion_dob("") == "-"
    assert format_notion_dob(None) == "-"
    assert format_notion_dob("()") == "-"

def test_parse_part_number():
    assert parse_part_number("Part 1") == 1
    assert parse_part_number("Part 2") == 2
    assert parse_part_number("Part 10") == 10
    assert parse_part_number("") == 1
    assert parse_part_number(None) == 1

def test_single_part_case():
    row = {
        "page_id": "p-1",
        "entry_id": "PL-1",
        "case_code": "BDO-HOM-001",
        "manifest_test_type": "LEGAL PATERNITY",
        "entry_part": "Part 1",
        "part_num": 1,
        "special_requests": "URGENT",
        "receiving_lab": "EUROFINS",
        "ready_for_manifest": True,
        "verified_reviewed": True,
        "participants": [
            {"name": "JOHN DOE", "dob": "14071980", "role": "FATHER", "ethnicity": "AFRICAN"},
            {"name": "JANE DOE", "dob": "10102015", "role": "CHILD", "ethnicity": "AFRICAN"}
        ]
    }
    cases = merge_multipart_cases([row])
    assert len(cases) == 1
    c = cases[0]
    assert c["bdo_tag"] == "BDO-HOM-001"
    assert c["test_type"] == "LEGAL PATERNITY"
    assert c["special_requests"] == "URGENT"
    assert c["receiving_lab"] == "EUROFINS"
    assert len(c["participants"]) == 2
    assert not c["is_multipart"]
    assert c["parts_merged"] == ["Part 1"]

def test_multipart_merging_5_plus_participants():
    # Part 1 has 4 participants (Father + 3 children)
    row_part1 = {
        "page_id": "p-1",
        "entry_id": "PL-10",
        "case_code": "BDO-HOM-555",
        "manifest_test_type": "LEGAL PATERNITY",
        "entry_part": "Part 1",
        "part_num": 1,
        "special_requests": "NONE",
        "receiving_lab": "ALPHABIOLABS",
        "ready_for_manifest": True,
        "verified_reviewed": True,
        "participants": [
            {"name": "KWAME MENSAH", "dob": "01011975", "role": "FATHER", "ethnicity": "AFRICAN"},
            {"name": "CHILD ONE", "dob": "02022010", "role": "CHILD", "ethnicity": "AFRICAN"},
            {"name": "CHILD TWO", "dob": "03032012", "role": "CHILD", "ethnicity": "AFRICAN"},
            {"name": "CHILD THREE", "dob": "04042014", "role": "CHILD", "ethnicity": "AFRICAN"}
        ]
    }
    # Part 2 has the 5th and 6th children (at P1 and P2 of Part 2)
    row_part2 = {
        "page_id": "p-2",
        "entry_id": "PL-11",
        "case_code": "BDO-HOM-555",
        "manifest_test_type": "LEGAL PATERNITY",
        "entry_part": "Part 2",
        "part_num": 2,
        "special_requests": "NONE",
        "receiving_lab": "ALPHABIOLABS",
        "ready_for_manifest": True,
        "verified_reviewed": True,
        "participants": [
            {"name": "CHILD FOUR", "dob": "05052016", "role": "CHILD", "ethnicity": "AFRICAN"},
            {"name": "CHILD FIVE", "dob": "06062018", "role": "CHILD", "ethnicity": "AFRICAN"}
        ]
    }
    
    # Intentionally pass them out of order to verify sorting by part_num
    cases = merge_multipart_cases([row_part2, row_part1])
    
    assert len(cases) == 1
    c = cases[0]
    assert c["bdo_tag"] == "BDO-HOM-555"
    assert c["is_multipart"] is True
    assert c["parts_merged"] == ["Part 1", "Part 2"]
    assert len(c["participants"]) == 6
    
    # Verify participant order
    assert c["participants"][0]["name"] == "KWAME MENSAH"
    assert c["participants"][1]["name"] == "CHILD ONE"
    assert c["participants"][2]["name"] == "CHILD TWO"
    assert c["participants"][3]["name"] == "CHILD THREE"
    assert c["participants"][4]["name"] == "CHILD FOUR"  # 5th participant
    assert c["participants"][5]["name"] == "CHILD FIVE"  # 6th participant

def test_multipart_merging_part3_and_beyond():
    # Part 1: Father + 3 children (4 participants)
    row_part1 = {
        "page_id": "p-1",
        "entry_id": "PL-101",
        "case_code": "BDO-HOM-999",
        "manifest_test_type": "LEGAL PATERNITY",
        "entry_part": "Part 1",
        "part_num": 1,
        "special_requests": "NONE",
        "receiving_lab": "EUROFINS",
        "ready_for_manifest": True,
        "verified_reviewed": True,
        "participants": [
            {"name": "FATHER LEAD", "dob": "01011970", "role": "ALLEGED FATHER", "ethnicity": "AFRICAN"},
            {"name": "CHILD 1", "dob": "01012010", "role": "CHILD", "ethnicity": "AFRICAN"},
            {"name": "CHILD 2", "dob": "02022011", "role": "CHILD", "ethnicity": "AFRICAN"},
            {"name": "CHILD 3", "dob": "03032012", "role": "CHILD", "ethnicity": "AFRICAN"}
        ]
    }
    # Part 2: 4 children (P1..P4 are children 4..7)
    row_part2 = {
        "page_id": "p-2",
        "entry_id": "PL-102",
        "case_code": "BDO-HOM-999",
        "manifest_test_type": "LEGAL PATERNITY",
        "entry_part": "Part 2",
        "part_num": 2,
        "special_requests": "NONE",
        "receiving_lab": "EUROFINS",
        "ready_for_manifest": True,
        "verified_reviewed": True,
        "participants": [
            {"name": "CHILD 4", "dob": "04042013", "role": "CHILD", "ethnicity": "AFRICAN"},
            {"name": "CHILD 5", "dob": "05052014", "role": "CHILD", "ethnicity": "AFRICAN"},
            {"name": "CHILD 6", "dob": "06062015", "role": "CHILD", "ethnicity": "AFRICAN"},
            {"name": "CHILD 7", "dob": "07072016", "role": "CHILD", "ethnicity": "AFRICAN"}
        ]
    }
    # Part 3: 2 children (P1 is child 8, P2 is child 9)
    row_part3 = {
        "page_id": "p-3",
        "entry_id": "PL-103",
        "case_code": "BDO-HOM-999",
        "manifest_test_type": "LEGAL PATERNITY",
        "entry_part": "Part 3",
        "part_num": 3,
        "special_requests": "NONE",
        "receiving_lab": "EUROFINS",
        "ready_for_manifest": True,
        "verified_reviewed": True,
        "participants": [
            {"name": "CHILD 8", "dob": "08082017", "role": "FATHER", "ethnicity": "AFRICAN"},  # Simulating raw default from Notion
            {"name": "CHILD 9", "dob": "09092018", "role": "CHILD", "ethnicity": "AFRICAN"}
        ]
    }

    # Pass rows scrambled: Part 3, Part 1, Part 2
    cases = merge_multipart_cases([row_part3, row_part1, row_part2])

    assert len(cases) == 1
    c = cases[0]
    assert c["bdo_tag"] == "BDO-HOM-999"
    assert c["is_multipart"] is True
    assert c["parts_merged"] == ["Part 1", "Part 2", "Part 3"]
    assert len(c["participants"]) == 10

    # Verify positions
    assert c["participants"][0]["name"] == "FATHER LEAD"
    assert c["participants"][0]["role"] == "ALLEGED FATHER"
    assert c["participants"][7]["name"] == "CHILD 7"
    # 9th participant is Part 3's P1 (Child 8)
    assert c["participants"][8]["name"] == "CHILD 8"
    assert c["participants"][8]["role"] == "CHILD"  # Normalized from FATHER to CHILD
    # 10th participant is Part 3's P2 (Child 9)
    assert c["participants"][9]["name"] == "CHILD 9"
    assert c["participants"][9]["role"] == "CHILD"


def test_excel_export_multipart(tmp_path):
    from exporter import create_excel_manifest
    import openpyxl

    cases = [{
        "bdo_tag": "BDO-TEST-001",
        "test_type": "LEGAL PATERNITY",
        "special_requests": "NONE",
        "participants": [
            {"name": f"PARTICIPANT {i}", "dob": f"0{i}012000", "role": "CHILD", "ethnicity": "AFRICAN"}
            for i in range(1, 7)
        ]
    }]
    out_file = str(tmp_path / "test_manifest.xlsx")
    create_excel_manifest(cases, out_file)

    wb = openpyxl.load_workbook(out_file)
    assert "BDO-TEST-001" in wb.sheetnames
    ws = wb["BDO-TEST-001"]
    assert ws["B2"].value == "BDO-TEST-001"
    # Row 7 is Participant 1, Row 12 is Participant 2... Row 32 is Participant 6
    # Check 6th participant role and name
    p6_start_row = 7 + 5 * 5
    assert ws.cell(row=p6_start_row, column=1).value == "Role"
    assert ws.cell(row=p6_start_row + 1, column=2).value == "PARTICIPANT 6"

def test_extract_prop_rollup_status():
    # Rollup array of select
    prop_select = {
        "type": "rollup",
        "rollup": {
            "type": "array",
            "array": [{"type": "select", "select": {"name": "Manifest Awaiting"}}]
        }
    }
    assert extract_prop_rollup_status(prop_select) == "Manifest Awaiting"

    # Rollup array of status
    prop_status = {
        "type": "rollup",
        "rollup": {
            "type": "array",
            "array": [{"type": "status", "status": {"name": "Dispatched to Laboratory"}}]
        }
    }
    assert extract_prop_rollup_status(prop_status) == "Dispatched to Laboratory"

    # Multiple statuses in rollup: prioritize non-'Manifest Awaiting'
    prop_mixed = {
        "type": "rollup",
        "rollup": {
            "type": "array",
            "array": [
                {"type": "select", "select": {"name": "Result Delivered"}},
                {"type": "select", "select": {"name": "Manifest Awaiting"}},
            ],
        },
    }
    assert extract_prop_rollup_status(prop_mixed) == "Result Delivered"

    prop_mixed_reverse = {
        "type": "rollup",
        "rollup": {
            "type": "array",
            "array": [
                {"type": "select", "select": {"name": "Manifest Awaiting"}},
                {"type": "select", "select": {"name": "Result Delivered"}},
            ],
        },
    }
    assert extract_prop_rollup_status(prop_mixed_reverse) == "Result Delivered"

    # Direct select
    prop_direct = {"type": "select", "select": {"name": "Manifest Awaiting"}}
    assert extract_prop_rollup_status(prop_direct) == "Manifest Awaiting"

    # Empty / None
    assert extract_prop_rollup_status(None) == ""
    assert extract_prop_rollup_status({}) == ""

def test_case_status_and_ready_for_manifest_propagation():
    row = {
        "page_id": "p-status-1",
        "entry_id": "PL-99",
        "case_code": "OVCD-EBD-021026-01",
        "manifest_test_type": "PRIVATE PATERNITY",
        "entry_part": "Part 1",
        "part_num": 1,
        "special_requests": "None",
        "receiving_lab": "ALPHABIO UK",
        "ready_for_manifest": False,
        "verified_reviewed": False,
        "case_status": "Manifest Awaiting",
        "participants": [
            {"name": "MUDASHIR KOLAWOLE", "dob": "14071980", "role": "ALLEGED FATHER", "ethnicity": "AFRICAN"},
            {"name": "CHILD ONE", "dob": "10102015", "role": "CHILD", "ethnicity": "AFRICAN"}
        ]
    }
    cases = merge_multipart_cases([row])
    assert len(cases) == 1
    c = cases[0]
    assert c["case_status"] == "Manifest Awaiting"
    assert c["ready_for_manifest"] is False

def test_process_raw_page_extracts_case_page_ids():
    raw_page = {
        "id": "notion-p-1",
        "properties": {
            "Entry ID": {"unique_id": {"prefix": "PL", "number": 100}},
            "Case Code": {"rich_text": [{"plain_text": "BDO-001"}]},
            "Case": {
                "type": "relation",
                "relation": [{"id": "case-rel-uuid-1"}]
            },
            "Case Status": {
                "type": "rollup",
                "rollup": {"type": "array", "array": [{"type": "select", "select": {"name": "Manifest Awaiting"}}]}
            },
            "P1 Name (Father or Lead)": {"title": [{"plain_text": "KWAME FATHER"}]},
            "P1 Role": {"select": {"name": "ALLEGED FATHER"}}
        }
    }
    processed = process_raw_page(raw_page)
    assert processed["page_id"] == "notion-p-1"
    assert processed["entry_id"] == "PL-100"
    assert processed["case_page_ids"] == ["case-rel-uuid-1"]

def test_multipart_merging_aggregates_case_page_ids():
    row1 = {
        "page_id": "p-1",
        "entry_id": "PL-1",
        "case_code": "CASE-MULTI",
        "manifest_test_type": "PRIVATE PATERNITY",
        "entry_part": "Part 1",
        "part_num": 1,
        "special_requests": "",
        "receiving_lab": "EUROFINS",
        "case_status": "Manifest Awaiting",
        "ready_for_manifest": True,
        "case_page_ids": ["case-uuid-xyz"],
        "participants": [{"name": "P1", "dob": "-", "role": "FATHER", "ethnicity": "AFRICAN"}]
    }
    row2 = {
        "page_id": "p-2",
        "entry_id": "PL-2",
        "case_code": "CASE-MULTI",
        "manifest_test_type": "PRIVATE PATERNITY",
        "entry_part": "Part 2",
        "part_num": 2,
        "special_requests": "",
        "receiving_lab": "EUROFINS",
        "case_status": "Manifest Awaiting",
        "ready_for_manifest": True,
        "case_page_ids": ["case-uuid-xyz"],
        "participants": [{"name": "P2", "dob": "-", "role": "CHILD", "ethnicity": "AFRICAN"}]
    }
    cases = merge_multipart_cases([row1, row2])
    assert len(cases) == 1
    assert cases[0]["case_page_ids"] == ["case-uuid-xyz"]
    assert cases[0]["page_ids"] == ["p-1", "p-2"]

def test_update_case_status_in_notion_mock(monkeypatch):
    from unittest.mock import MagicMock
    from notion_service import update_case_status_in_notion, _manifest_cache
    import httpx

    # Set mock cache
    _manifest_cache["data"] = ["dummy"]

    # Mock client.patch response
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    def mock_patch(self, url, *args, **kwargs):
        assert "pages/case-123" in url or "pages/case-456" in url
        return mock_resp

    monkeypatch.setattr(httpx.Client, "patch", mock_patch)

    res = update_case_status_in_notion(
        case_page_ids=["case-123", "case-456", "case-123"],  # duplicates should be filtered
        target_status="Dispatched to Laboratory",
        api_key="mock-key"
    )

    assert res["total"] == 2
    assert res["updated"] == 2
    assert res["failed"] == 0
    # Cache should be invalidated
    assert _manifest_cache["data"] is None

def test_update_case_status_empty():
    from notion_service import update_case_status_in_notion
    res = update_case_status_in_notion([])
    assert res["total"] == 0
    assert res["updated"] == 0

def test_notion_token_env_support(monkeypatch):
    from notion_service import get_notion_headers
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    monkeypatch.setenv("NOTION_TOKEN", "secret_token_123")
    headers = get_notion_headers()
    assert headers["Authorization"] == "Bearer secret_token_123"


def test_fetch_manifest_cases_excludes_non_manifest_awaiting(monkeypatch):
    from notion_service import fetch_manifest_cases, clear_manifest_cache
    clear_manifest_cache()

    dummy_pages = [
        {
            "id": "page-1",
            "properties": {
                "Case Code": {"type": "formula", "formula": {"type": "string", "string": "CD-BDO-201026-01"}},
                "Manifest Test Type": {"type": "formula", "formula": {"type": "string", "string": "LEGAL PATERNITY"}},
                "Case Status": {
                    "type": "rollup",
                    "rollup": {
                        "type": "array",
                        "array": [{"type": "select", "select": {"name": "Manifest Awaiting"}}],
                    },
                },
                "Receiving Lab": {
                    "type": "rollup",
                    "rollup": {
                        "type": "array",
                        "array": [{"type": "select", "select": {"name": "Eurofins Germany"}}],
                    },
                },
                "P1 Name (Father or Lead)": {"type": "title", "title": [{"plain_text": "John Doe"}]},
            },
        },
        {
            "id": "page-2",
            "properties": {
                "Case Code": {"type": "formula", "formula": {"type": "string", "string": "PP-EBD-011026-02"}},
                "Manifest Test Type": {"type": "formula", "formula": {"type": "string", "string": "PRIVATE PATERNITY"}},
                "Case Status": {
                    "type": "rollup",
                    "rollup": {
                        "type": "array",
                        "array": [
                            {"type": "select", "select": {"name": "Result Delivered"}},
                            {"type": "select", "select": {"name": "Manifest Awaiting"}},
                        ],
                    },
                },
                "Receiving Lab": {
                    "type": "rollup",
                    "rollup": {
                        "type": "array",
                        "array": [{"type": "select", "select": {"name": "Eurofins Germany"}}],
                    },
                },
                "P1 Name (Father or Lead)": {"type": "title", "title": [{"plain_text": "Simon Oelmann"}]},
            },
        },
    ]

    import notion_service
    monkeypatch.setattr(notion_service, "query_notion_database", lambda *args, **kwargs: dummy_pages)

    res = fetch_manifest_cases(filter_mode="manifest_batch", force_refresh=True)
    tags = [c["bdo_tag"] for c in res["cases"]]

    assert "CD-BDO-201026-01" in tags
    assert "PP-EBD-011026-02" not in tags
    assert len(res["cases"]) == 1
    assert res["cases"][0]["case_status"] == "Manifest Awaiting"


def test_process_raw_page_extracts_compound_formula():
    raw_page = {
        "id": "notion-p-compound",
        "properties": {
            "Entry ID": {"type": "unique_id", "unique_id": {"prefix": "PL", "number": 246}},
            "Case Code": {"type": "rich_text", "rich_text": [{"plain_text": "IM-EBD-161026-03"}]},
            "Manifest Test Type": {
                "type": "formula",
                "formula": {"type": "string", "string": "LEGAL PATERNITY + MATERNITY"}
            },
            "P1 Name (Father or Lead)": {"type": "title", "title": [{"plain_text": "KWAME FATHER"}]},
            "P1 Role": {"type": "select", "select": {"name": "ALLEGED FATHER"}}
        }
    }
    processed = process_raw_page(raw_page)
    assert processed["entry_id"] == "PL-246"
    assert processed["case_code"] == "IM-EBD-161026-03"
    assert processed["manifest_test_type"] == "LEGAL PATERNITY + MATERNITY"


def test_merge_multipart_cases_preserves_compound_test_type():
    row1 = {
        "page_id": "p-1",
        "entry_id": "PL-246",
        "case_code": "IM-EBD-161026-03",
        "manifest_test_type": "LEGAL PATERNITY + MATERNITY",
        "entry_part": "Part 1",
        "part_num": 1,
        "special_requests": "",
        "receiving_lab": "EUROFINS",
        "case_status": "Manifest Awaiting",
        "ready_for_manifest": True,
        "case_page_ids": [],
        "participants": [{"name": "FATHER", "dob": "01011985", "role": "ALLEGED FATHER", "ethnicity": "AFRICAN"}]
    }
    row2 = {
        "page_id": "p-2",
        "entry_id": "PL-247",
        "case_code": "IM-EBD-161026-03",
        "manifest_test_type": "LEGAL PATERNITY + MATERNITY",
        "entry_part": "Part 2",
        "part_num": 2,
        "special_requests": "",
        "receiving_lab": "EUROFINS",
        "case_status": "Manifest Awaiting",
        "ready_for_manifest": True,
        "case_page_ids": [],
        "participants": [
            {"name": "MOTHER", "dob": "02021988", "role": "MOTHER", "ethnicity": "AFRICAN"},
            {"name": "CHILD", "dob": "03032015", "role": "CHILD", "ethnicity": "AFRICAN"}
        ]
    }
    cases = merge_multipart_cases([row1, row2])
    assert len(cases) == 1
    assert cases[0]["bdo_tag"] == "IM-EBD-161026-03"
    assert cases[0]["test_type"] == "LEGAL PATERNITY + MATERNITY"
    assert len(cases[0]["participants"]) == 3


def test_merge_multipart_cases_falls_back_to_part2_test_type():
    row1 = {
        "page_id": "p-1",
        "entry_id": "PL-246",
        "case_code": "IM-EBD-161026-03",
        "manifest_test_type": "",  # Blank in row 1
        "entry_part": "Part 1",
        "part_num": 1,
        "special_requests": "",
        "receiving_lab": "EUROFINS",
        "case_status": "Manifest Awaiting",
        "ready_for_manifest": True,
        "case_page_ids": [],
        "participants": [{"name": "FATHER", "dob": "01011985", "role": "ALLEGED FATHER", "ethnicity": "AFRICAN"}]
    }
    row2 = {
        "page_id": "p-2",
        "entry_id": "PL-247",
        "case_code": "IM-EBD-161026-03",
        "manifest_test_type": "LEGAL PATERNITY + MATERNITY",  # Present in row 2
        "entry_part": "Part 2",
        "part_num": 2,
        "special_requests": "",
        "receiving_lab": "EUROFINS",
        "case_status": "Manifest Awaiting",
        "ready_for_manifest": True,
        "case_page_ids": [],
        "participants": [{"name": "CHILD", "dob": "03032015", "role": "CHILD", "ethnicity": "AFRICAN"}]
    }
    cases = merge_multipart_cases([row1, row2])
    assert len(cases) == 1
    assert cases[0]["test_type"] == "LEGAL PATERNITY + MATERNITY"





