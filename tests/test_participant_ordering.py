import pytest
from notion_service import get_role_sort_rank, merge_multipart_cases

def test_role_priority_ranking():
    # Rank 1: Fathers
    assert get_role_sort_rank("ALLEGED FATHER") == 1
    assert get_role_sort_rank("FATHER") == 1
    
    # Rank 2: Mothers
    assert get_role_sort_rank("MOTHER") == 2
    assert get_role_sort_rank("ALLEGED MOTHER") == 2
    assert get_role_sort_rank("MOTHER (NOT ALLEGED)") == 2
    
    # Rank 3: Adult Relatives / Grandparents
    assert get_role_sort_rank("ALLEGED GRANDPARENT") == 3
    assert get_role_sort_rank("MALE 1") == 3
    assert get_role_sort_rank("MALE RELATIVE") == 3
    
    # Rank 4: Children
    assert get_role_sort_rank("CHILD") == 4
    assert get_role_sort_rank("GRANDCHILD") == 4

def test_trio_case_ordering():
    # Case where Child was entered at P2 and Mother at P3
    row = {
        "page_id": "p-trio",
        "entry_id": "PL-TRIO",
        "case_code": "CD-TRIO-01",
        "manifest_test_type": "LEGAL PATERNITY",
        "entry_part": "Part 1",
        "part_num": 1,
        "special_requests": "NONE",
        "receiving_lab": "EUROFINS GERMANY",
        "ready_for_manifest": True,
        "verified_reviewed": True,
        "participants": [
            {"name": "FELIX FATHER", "dob": "23091993", "role": "ALLEGED FATHER", "ethnicity": "AFRICAN"},
            {"name": "CHILD X", "dob": "26072026", "role": "CHILD", "ethnicity": "AFRICAN"},
            {"name": "BENEDICTA MOTHER", "dob": "13122005", "role": "MOTHER", "ethnicity": "AFRICAN"}
        ]
    }
    cases = merge_multipart_cases([row])
    assert len(cases) == 1
    parts = cases[0]["participants"]
    assert len(parts) == 3
    
    # Must be: Father (1st) -> Mother (2nd) -> Child (3rd)
    assert parts[0]["name"] == "FELIX FATHER"
    assert parts[0]["role"] == "ALLEGED FATHER"
    assert parts[1]["name"] == "BENEDICTA MOTHER"
    assert parts[1]["role"] == "MOTHER"
    assert parts[2]["name"] == "CHILD X"
    assert parts[2]["role"] == "CHILD"

def test_trio_case_multiple_children_ordering():
    # Mother at P4, Father at P1, Children at P2 & P3
    row = {
        "page_id": "p-trio-2",
        "entry_id": "PL-TRIO-2",
        "case_code": "CD-TRIO-02",
        "manifest_test_type": "PRIVATE PATERNITY",
        "entry_part": "Part 1",
        "part_num": 1,
        "special_requests": "NONE",
        "receiving_lab": "EUROFINS GERMANY",
        "ready_for_manifest": True,
        "verified_reviewed": True,
        "participants": [
            {"name": "JOHN FATHER", "dob": "01011980", "role": "ALLEGED FATHER", "ethnicity": "AFRICAN"},
            {"name": "CHILD 1", "dob": "01012015", "role": "CHILD", "ethnicity": "AFRICAN"},
            {"name": "CHILD 2", "dob": "01012018", "role": "CHILD", "ethnicity": "AFRICAN"},
            {"name": "MARY MOTHER", "dob": "01011985", "role": "MOTHER", "ethnicity": "AFRICAN"}
        ]
    }
    cases = merge_multipart_cases([row])
    parts = cases[0]["participants"]
    assert len(parts) == 4
    
    # Father (1st) -> Mother (2nd) -> Child 1 (3rd) -> Child 2 (4th)
    assert parts[0]["name"] == "JOHN FATHER"
    assert parts[1]["name"] == "MARY MOTHER"
    assert parts[2]["name"] == "CHILD 1"
    assert parts[3]["name"] == "CHILD 2"
