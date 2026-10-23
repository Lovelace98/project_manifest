"""
Integrity, Layout Anomaly Detection, and Notion Cross-Verification Engine.
Provides safety checks for laboratory layout shifts, participant discrepancies,
and automated dual 'AND' classification for report types.
"""
from __future__ import annotations

import os
import re
import time
import logging
from typing import Any
import httpx
import pymupdf as fitz

from report_bot.domain import CaseType, LabSource, Participant, ReportData, normalize_dob_value
from notion_service import (
    get_notion_headers,
    execute_notion_request,
    NOTION_BASE_URL,
    extract_prop_text,
    extract_prop_select,
    extract_prop_date,
    format_notion_dob,
)

logger = logging.getLogger(__name__)

PERSONAL_PREFIXES = {"SS", "PP", "OVPP"}
CERTIFIED_PREFIXES = {"CD"}


def detect_layout_anomalies(
    pdf_bytes: bytes,
    lab_source: LabSource,
    case_type: CaseType,
    test_type: str = "",
) -> list[str]:
    """
    Inspects PDF geometry and visual anchors to detect if a motherlab has
    shifted its original layout, preventing blind redacting/cropping errors.
    """
    warnings: list[str] = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        return [f"Unable to inspect PDF structure: {exc}"]

    try:
        if doc.page_count < 1:
            return ["PDF contains no readable pages"]
        first_page = doc[0]
        page_width, page_height = first_page.rect.width, first_page.rect.height

        if lab_source == LabSource.EUROFINS:
            # 1. Certificate title heading in upper region
            title_found = False
            for block in first_page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    line_text = "".join(span.get("text", "") for span in line.get("spans", [])).upper()
                    if "TEST CERTIFICATE" in line_text:
                        title_found = True
                        y0 = min(span["bbox"][1] for span in line.get("spans", []))
                        if not (70 <= y0 <= 260):
                            warnings.append(
                                f"Layout Shift: Title anchor is at unexpected y-coordinate ({y0:.1f} pt)"
                            )
                        break
                if title_found:
                    break
            if not title_found:
                warnings.append(
                    "Layout Shift: 'TEST CERTIFICATE' title heading not located in expected position"
                )

            # 2. Table column header bounds
            words = first_page.get_text("words")
            sample_words = [w for w in words if w[4].upper() == "SAMPLE"]
            if not sample_words or not any(120 <= w[1] <= 390 for w in sample_words):
                warnings.append(
                    "Layout Shift: Participant table header ('SAMPLE') not found in expected bounds"
                )

            # 3. Personal warning anchor check ("Results:")
            if case_type == CaseType.PERSONAL:
                results_hits = []
                for p in doc:
                    hits = p.search_for("Results:")
                    if hits:
                        results_hits.extend(hits)
                if not results_hits:
                    warnings.append(
                        "Layout Shift: 'Results:' heading missing; personal warning overlay may misalign"
                    )
                else:
                    r_y0 = results_hits[0].y0
                    if not (150 <= r_y0 <= 720):
                        warnings.append(
                            f"Layout Shift: 'Results:' heading is at abnormal coordinate ({r_y0:.1f} pt)"
                        )

            # 4. Eurofins footer disclaimer anchor (scanned across all pages for multi-page reports)
            disclaimer_hits = []
            for p in doc:
                hits = p.search_for("carries out all analyses")
                if hits:
                    disclaimer_hits.extend(hits)
            if not disclaimer_hits:
                warnings.append(
                    "Layout Shift: Eurofins footer disclaimer anchor missing; fallback footer crop applied"
                )

        elif lab_source == LabSource.UFC:
            doc_text = " ".join(p.get_text() for p in doc)
            # 1. Order ID check
            if "Order ID:" not in doc_text:
                warnings.append("Layout Shift: UFC 'Order ID:' header anchor missing")

            # 2. UFC Footer anchor check
            if "Universal Forensics" not in doc_text:
                warnings.append("Layout Shift: UFC 'Universal Forensics' footer anchor missing")

            # 3. Chain of Custody verification for UFC
            if case_type == CaseType.CERTIFIED_LEGAL:
                if "No Chain of Custody" in doc_text:
                    warnings.append(
                        "Layout Conflict: Motherlab UFC printed 'No Chain of Custody', but case is classified as Certified / Legal"
                    )
                elif "Chain of Custody" not in doc_text:
                    warnings.append("Layout Shift: UFC 'Chain of Custody' landmark text not found")
            elif case_type == CaseType.PERSONAL:
                if "Chain of Custody" in doc_text and "No Chain of Custody" not in doc_text:
                    warnings.append(
                        "Layout Conflict: Motherlab UFC printed legal 'Chain of Custody', but case is classified as Personal"
                    )

            # 4. Personal warning collision check against UFC_PERSONAL_WARNING_RECT (158.0 to 179.0 pt on page 1)
            if case_type == CaseType.PERSONAL:
                words = first_page.get_text("words")
                colliding_words = [
                    w[4] for w in words
                    if 158.0 <= w[1] <= 179.0 and w[0] > 90 and w[4].upper() not in {"PERSONAL", "USE", "ONLY", "NOT", "AN", "OFFICIAL", "DOCUMENT", "-"}
                ]
                if colliding_words:
                    warnings.append(
                        f"Potential Collision: Participant table text detected in personal disclaimer area ({', '.join(colliding_words[:3])})"
                    )

        elif lab_source == LabSource.HONG_KONG:
            text = first_page.get_text()
            if not any(k in text for k in ("Test ID", "Collection Date", "Report Date")):
                warnings.append("Layout Shift: Hong Kong report header landmarks missing")

        return warnings
    finally:
        doc.close()


def _post_with_retry(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    max_retries: int = 4,
) -> httpx.Response | None:
    try:
        return execute_notion_request(
            client=client,
            method="POST",
            url=url,
            headers=headers,
            payload=payload,
            timeout=20.0,
            max_retries=max_retries,
        )
    except Exception as exc:
        logger.debug("Notion request failed after retries: %s", exc)
        return None


def fetch_notion_verification_data(
    case_code: str,
    api_key: str | None = None,
    cases_db_id: str | None = None,
    part_db_id: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """
    Queries Notion Cases DB and Participant Lists DB for a given Case Code.
    Returns case metadata, Test/Service multi-select tags, and recorded participants.
    """
    clean_code = case_code.strip().upper()
    base_code = re.sub(r"_\d+$", "", clean_code)

    cases_db = cases_db_id or os.getenv("NOTION_CASES_DB_ID", "382c226a-2e86-48f1-b70e-c4e9e8f62d1d")
    part_db = part_db_id or os.getenv("NOTION_PARTICIPANT_DB_ID", "281dbd83-c9c2-4342-8a93-92574ef8ed15")

    try:
        headers = get_notion_headers(api_key)
    except Exception as exc:
        logger.warning("Notion credentials unavailable for verification: %s", exc)
        return {"notion_available": False, "error": str(exc), "found": False}

    should_close_client = False
    if client is None:
        client = httpx.Client(timeout=20.0)
        should_close_client = True

    result: dict[str, Any] = {
        "notion_available": True,
        "found": False,
        "case_page_id": None,
        "manifest_test_type": None,
        "test_or_service": [],
        "participants": [],
        "error": None,
    }

    try:
        candidates = list(dict.fromkeys([clean_code, base_code]))

        # 1. Query Cases Database for 'Test or Service'
        case_found = False
        for candidate in candidates:
            if not candidate:
                continue
            url = f"{NOTION_BASE_URL}/databases/{cases_db}/query"
            payload = {
                "page_size": 2,
                "filter": {
                    "property": "title",
                    "title": {"contains": candidate},
                },
            }
            resp = _post_with_retry(client, url, headers, payload)
            if resp is not None and resp.status_code == 200:
                cases_res = resp.json().get("results", [])
                if cases_res:
                    case_page = cases_res[0]
                    result["case_page_id"] = case_page.get("id")
                    result["found"] = True
                    case_found = True
                    
                    # Extract multi_select tags from "Test or Service"
                    props = case_page.get("properties", {})
                    tos_prop = props.get("Test or Service", {})
                    if tos_prop.get("type") == "multi_select":
                        result["test_or_service"] = [
                            opt.get("name", "").strip()
                            for opt in tos_prop.get("multi_select", [])
                            if opt.get("name")
                        ]
                    break

        # 2. Query Participant DB for recorded participants
        for candidate in candidates:
            if not candidate:
                continue
            url = f"{NOTION_BASE_URL}/databases/{part_db}/query"
            payload = {
                "page_size": 5,
                "filter": {
                    "property": "Case Code",
                    "formula": {
                        "string": {
                            "equals": candidate
                        }
                    },
                },
            }
            resp = _post_with_retry(client, url, headers, payload)
            if resp is not None and resp.status_code == 200:
                part_rows = resp.json().get("results", [])
                if part_rows:
                    result["found"] = True
                    notion_parts: list[dict[str, Any]] = []
                    for row in part_rows:
                        if not result.get("case_page_id"):
                            case_rel = row.get("properties", {}).get("Case", {}).get("relation", [])
                            if case_rel:
                                result["case_page_id"] = case_rel[0].get("id")
                        props = row.get("properties", {})
                        if not result.get("manifest_test_type"):
                            m_type = extract_prop_text(props.get("Manifest Test Type")).strip().upper()
                            if m_type:
                                result["manifest_test_type"] = m_type
                        # P1
                        p1_name = extract_prop_text(props.get("P1 Name (Father or Lead)")).upper()
                        if p1_name:
                            p1_dob_raw = extract_prop_date(props.get("P1 Date of Birth"))
                            p1_role = extract_prop_select(props.get("P1 Role")).upper() or "ALLEGED FATHER"
                            notion_parts.append({
                                "name": p1_name,
                                "dob": normalize_dob_value(p1_dob_raw),
                                "role": p1_role,
                            })
                        # P2, P3, P4
                        for idx in range(2, 5):
                            p_name = extract_prop_text(props.get(f"P{idx} Name")).upper()
                            if p_name:
                                p_dob_raw = extract_prop_date(props.get(f"P{idx} Date of Birth"))
                                p_role = extract_prop_select(props.get(f"P{idx} Role")).upper() or "CHILD"
                                notion_parts.append({
                                    "name": p_name,
                                    "dob": normalize_dob_value(p_dob_raw),
                                    "role": p_role,
                                })
                    result["participants"] = notion_parts
                    break

        return result
    finally:
        if should_close_client:
            client.close()


def determine_report_classification(
    case_code: str,
    notion_data: dict[str, Any] | None,
    default_case_type: CaseType = CaseType.ISSUE,
) -> dict[str, Any]:
    """
    Applies the dual 'AND' operator check between Case Code prefix and Notion 'Test or Service'.
    Personal cases: prefix in (SS, PP, OVPP) AND Notion lacks 'Legal'.
    Certified cases: prefix is 'CD' AND Notion has 'Legal'.
    Any mismatch forces a manual review state with explanatory audit trail.
    """
    clean_code = case_code.strip().upper()
    prefix = clean_code.split("-")[0] if "-" in clean_code else ""

    is_prefix_personal = prefix in PERSONAL_PREFIXES
    is_prefix_certified = prefix in CERTIFIED_PREFIXES

    # If Notion is offline or case wasn't found
    if not notion_data or not notion_data.get("found"):
        if is_prefix_personal:
            return {
                "case_type": CaseType.PERSONAL,
                "verified": False,
                "mismatch": False,
                "needs_manual_review": False,
                "reason": f"Auto-classified PERSONAL from prefix '{prefix}' (Case not found in Notion database).",
                "badge_label": f"Personal ({prefix})",
                "badge_status": "neutral",
            }
        elif is_prefix_certified:
            return {
                "case_type": CaseType.CERTIFIED_LEGAL,
                "verified": False,
                "mismatch": False,
                "needs_manual_review": False,
                "reason": f"Auto-classified CERTIFIED / LEGAL from prefix '{prefix}' (Case not found in Notion database).",
                "badge_label": f"Legal ({prefix})",
                "badge_status": "neutral",
            }
        else:
            is_issue = default_case_type == CaseType.ISSUE
            return {
                "case_type": default_case_type,
                "verified": False,
                "mismatch": True,
                "needs_manual_review": True,
                "reason": (
                    f"Unrecognized case code prefix '{prefix}' and case not found in Notion. "
                    f"Report type set to Issue: select Personal or Certified / Legal before approval."
                    if is_issue
                    else f"Unrecognized case code prefix '{prefix}' and case not found in Notion."
                ),
                "badge_label": "Issue (Unclassified)" if is_issue else "Review Required",
                "badge_status": "danger" if is_issue else "warning",
            }

    notion_services = [str(s).upper() for s in notion_data.get("test_or_service", [])]
    has_legal_in_notion = any("LEGAL" in s for s in notion_services)
    services_summary = ", ".join(notion_data.get("test_or_service", [])) or "None listed"

    # Dual AND Condition 1: PERSONAL
    # Prefix in (SS, PP, OVPP) AND Notion Test or Service does NOT contain Legal.
    # Note: Test or Service may contain other tags (e.g. Additional party, Self-Collection Kit, Discreet, etc.).
    # As long as 'Legal' is NOT present, it is confirmed Personal.
    if is_prefix_personal and not has_legal_in_notion:
        return {
            "case_type": CaseType.PERSONAL,
            "verified": True,
            "mismatch": False,
            "needs_manual_review": False,
            "reason": f"Verified Personal: Prefix '{prefix}' + Notion services: '{services_summary}' (No 'Legal' tag).",
            "badge_label": "Personal (Notion Verified)",
            "badge_status": "success",
        }

    # Dual AND Condition 2: CERTIFIED / LEGAL
    # Prefix is 'CD' AND Notion Test or Service CONTAINS Legal.
    # Note: Test or Service may contain other tags (e.g. Paternity, Additional parties, etc.).
    # As long as 'Legal' IS present, it is confirmed Certified / Legal.
    if is_prefix_certified and has_legal_in_notion:
        return {
            "case_type": CaseType.CERTIFIED_LEGAL,
            "verified": True,
            "mismatch": False,
            "needs_manual_review": False,
            "reason": f"Verified Certified / Legal: Prefix '{prefix}' + Notion services: '{services_summary}' (Contains 'Legal').",
            "badge_label": "Legal (Notion Verified)",
            "badge_status": "success",
        }

    # Conflict / Mismatch: Prefix vs. Notion disagree
    if is_prefix_personal and has_legal_in_notion:
        reason = (
            f"Classification Mismatch: Case prefix '{prefix}' indicates Personal, but Notion lists "
            f"'{services_summary}' (contains 'Legal'). Manual confirmation required."
        )
        assigned = CaseType.CERTIFIED_LEGAL  # default towards caution
    elif is_prefix_certified and not has_legal_in_notion:
        reason = (
            f"Classification Mismatch: Case prefix '{prefix}' indicates Certified/Legal, but Notion lists "
            f"'{services_summary}' (lacks 'Legal' tag). Manual confirmation required."
        )
        assigned = CaseType.CERTIFIED_LEGAL
    elif not is_prefix_personal and not is_prefix_certified:
        assigned = CaseType.CERTIFIED_LEGAL if has_legal_in_notion else CaseType.PERSONAL
        reason = (
            f"Prefix '{prefix or 'None'}' unrecognized. Notion lists '{services_summary}' "
            f"({'contains Legal' if has_legal_in_notion else 'no Legal tag'}) -> Assigned {assigned.value}. "
            f"Manual confirmation required."
        )
    else:
        reason = (
            f"Classification Mismatch: Case prefix '{prefix}' and Notion services '{services_summary}' "
            f"do not form a clean match. Manual review required."
        )
        assigned = default_case_type

    return {
        "case_type": assigned,
        "verified": False,
        "mismatch": True,
        "needs_manual_review": True,
        "reason": reason,
        "badge_label": "Type Conflict (Review Needed)",
        "badge_status": "warning",
    }


def normalize_test_type(val: str) -> str | None:
    if not val:
        return None
    s = str(val).strip().upper()
    # Strip prefixes like "LEGAL ", "PRIVATE ", "PERSONAL " and suffixes like " TEST", " CERTIFICATE"
    s = re.sub(r"^(LEGAL|PRIVATE|PERSONAL)\s+", "", s)
    s = re.sub(r"\s+(TEST|CERTIFICATE)$", "", s)
    s = s.strip()

    if "PATERNITY" in s and "MATERNITY" in s:
        return "PATERNITY + MATERNITY"
    if "PATERNITY" in s:
        if "PRENATAL" in s:
            return "PRENATAL PATERNITY"
        return "PATERNITY"
    if "MATERNITY" in s:
        return "MATERNITY"
    if "SINGLE PROFILE" in s or "DNA PROFILE" in s:
        return "SINGLE PROFILE"
    if "SIBLING" in s:
        return "SIBLINGSHIP"
    if "AVUNCULAR" in s or "AUNT" in s or "UNCLE" in s:
        return "AVUNCULAR"
    if "GRANDPARENT" in s or "GRAND" in s:
        return "GRANDPARENTAGE"
    if "Y-CHROMOSOME" in s or "Y CHROMOSOME" in s or "Y-STR" in s:
        return "Y-CHROMOSOME"
    if "MITOCHONDRIAL" in s or "MTDNA" in s:
        return "MITOCHONDRIAL"
    if "ZYGOSITY" in s or "TWIN" in s:
        return "ZYGOSITY"
    if "GENDER" in s:
        return "GENDER"
    return s if s else None


def determine_test_type(
    current_test_type: str,
    participants: list[Participant],
    notion_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Reconciles the report test type against Notion database metadata
    ('Manifest Test Type' from Participant DB and 'Test or Service' from Cases DB)
    and participant roles.
    Enforces the Single Profile guardrail (cannot be Single Profile if >1 participant).
    """
    current_norm = normalize_test_type(current_test_type) or (current_test_type.strip().upper() if current_test_type else "PATERNITY")

    # 1. Evaluate participant roles
    roles = " ".join(p.role.upper() for p in participants)
    has_alleged_father = "ALLEGED FATHER" in roles or ("FATHER" in roles and "ALLEGED" in roles)
    has_alleged_mother = "ALLEGED MOTHER" in roles or ("MOTHER" in roles and "ALLEGED" in roles and "FATHER" not in roles)
    has_child = any(r in roles for r in ("CHILD", "DAUGHTER", "SON"))

    role_inferred: str | None = None
    if has_alleged_father and has_alleged_mother and has_child:
        role_inferred = "PATERNITY + MATERNITY"
    elif has_alleged_father:
        role_inferred = "PATERNITY"
    elif has_alleged_mother:
        role_inferred = "MATERNITY"
    elif "SIBLING" in roles:
        role_inferred = "SIBLINGSHIP"
    elif "AUNT" in roles or "UNCLE" in roles:
        role_inferred = "AVUNCULAR"
    elif "GRAND" in roles:
        role_inferred = "GRANDPARENTAGE"
    elif "Y-CHROMOSOME" in roles or "Y-STR" in roles:
        role_inferred = "Y-CHROMOSOME"

    # 2. Extract candidate from Notion
    notion_candidate: str | None = None
    if notion_data and notion_data.get("found"):
        m_type = notion_data.get("manifest_test_type")
        if m_type:
            notion_candidate = normalize_test_type(m_type)

        if not notion_candidate:
            for tag in notion_data.get("test_or_service", []):
                norm_tag = normalize_test_type(tag)
                if norm_tag and norm_tag not in (
                    "LEGAL", "PRIVATE", "PERSONAL", "ADDITIONAL PARTIES",
                    "ADDITIONAL PARTY", "SELF-COLLECTION KIT", "DISCREET"
                ):
                    notion_candidate = norm_tag
                    break

    # 3. Single Profile Guardrail: multiple participants cannot be Single Profile
    is_multi_participant = len(participants) > 1
    if current_norm == "SINGLE PROFILE" and is_multi_participant:
        fallback = notion_candidate or role_inferred or "PATERNITY"
        return {
            "test_type": fallback,
            "verified": bool(notion_candidate and notion_candidate == fallback),
            "needs_manual_review": False,
            "reason": f"Auto-corrected Single Profile to {fallback}: report contains {len(participants)} participants.",
        }

    # 4. Notion reconciliation
    if notion_candidate:
        if notion_candidate == current_norm:
            return {
                "test_type": current_norm,
                "verified": True,
                "needs_manual_review": False,
                "reason": None,
            }

        # If current was inferred without explicit certificate title, or Notion enriches it
        if role_inferred and notion_candidate == role_inferred:
            return {
                "test_type": notion_candidate,
                "verified": True,
                "needs_manual_review": False,
                "reason": None,
            }

        # Direct conflict between Notion and participant roles (e.g. Notion says Maternity but report has Father)
        if role_inferred and role_inferred != notion_candidate:
            return {
                "test_type": current_norm,
                "verified": False,
                "needs_manual_review": True,
                "reason": f"Test Type Mismatch: Notion indicates '{notion_candidate}' but report participants indicate '{role_inferred}'. Manual review required.",
            }

        # Otherwise adopt Notion's explicit test type
        return {
            "test_type": notion_candidate,
            "verified": True,
            "needs_manual_review": False,
            "reason": None,
        }

    # If Notion is unavailable or case not found in Notion
    final_type = role_inferred or current_norm
    if final_type == "SINGLE PROFILE" and is_multi_participant:
        final_type = "PATERNITY"

    return {
        "test_type": final_type,
        "verified": False,
        "needs_manual_review": False,
        "reason": None,
    }


def cross_verify_participants(
    extracted: list[Participant],
    notion_participants: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Compares extracted PDF participant details with recorded Notion records.
    Detects missing names, role discrepancies, or mismatched dates of birth.
    """
    if not notion_participants:
        return {
            "verified": True,
            "has_notion_records": False,
            "discrepancies": [],
            "comparison": [],
        }

    discrepancies: list[str] = []
    comparison: list[dict[str, Any]] = []

    # Count check
    if len(extracted) != len(notion_participants):
        discrepancies.append(
            f"Participant count mismatch: PDF report has {len(extracted)} participant(s), "
            f"but Notion records {len(notion_participants)}."
        )

    # Participant matching by normalized name
    used_notion_indices: set[int] = set()

    for ext in extracted:
        norm_ext_name = re.sub(r"[^A-Z]", "", ext.full_name.upper())
        matched_idx = None

        # Try exact or substring match
        for idx, n_part in enumerate(notion_participants):
            if idx in used_notion_indices:
                continue
            norm_notion_name = re.sub(r"[^A-Z]", "", str(n_part.get("name", "")).upper())
            if norm_ext_name == norm_notion_name or norm_ext_name in norm_notion_name or norm_notion_name in norm_ext_name:
                matched_idx = idx
                break

        if matched_idx is not None:
            used_notion_indices.add(matched_idx)
            n_part = notion_participants[matched_idx]
            
            # Check DOB
            ext_dob_clean = re.sub(r"[^0-9]", "", str(ext.date_of_birth))
            notion_dob_raw = str(n_part.get("dob", "-"))
            notion_dob_clean = re.sub(r"[^0-9]", "", notion_dob_raw)
            dob_match = True
            if ext_dob_clean and notion_dob_clean and ext_dob_clean != "-" and notion_dob_clean != "-":
                if ext_dob_clean != notion_dob_clean:
                    dob_match = False
                    discrepancies.append(
                        f"DOB discrepancy for {ext.full_name}: PDF has '{ext.date_of_birth}', Notion records '{notion_dob_raw}'."
                    )

            # Check Role
            ext_role_norm = ext.role.upper().replace("ALLEGED ", "").strip()
            notion_role_norm = str(n_part.get("role", "")).upper().replace("ALLEGED ", "").strip()
            role_match = (
                (ext_role_norm == notion_role_norm)
                or (not ext_role_norm)
                or (not notion_role_norm)
                or (ext_role_norm in ("PARTICIPANT", "INDIVIDUAL", "SELF"))
                or (notion_role_norm in ("PARTICIPANT", "INDIVIDUAL", "SELF"))
            )
            if not role_match:
                discrepancies.append(
                    f"Role discrepancy for {ext.full_name}: PDF lists '{ext.role}', Notion records '{n_part.get('role')}'."
                )

            comparison.append({
                "pdf_name": ext.full_name,
                "notion_name": n_part.get("name"),
                "pdf_role": ext.role,
                "notion_role": n_part.get("role"),
                "pdf_dob": ext.date_of_birth,
                "notion_dob": notion_dob_raw,
                "dob_match": dob_match,
                "role_match": role_match,
                "status": "MATCHED" if (dob_match and role_match) else "DISCREPANCY",
            })
        else:
            discrepancies.append(f"Participant '{ext.full_name}' ({ext.role}) in PDF was not found in Notion.")
            comparison.append({
                "pdf_name": ext.full_name,
                "notion_name": None,
                "pdf_role": ext.role,
                "notion_role": None,
                "pdf_dob": ext.date_of_birth,
                "notion_dob": None,
                "dob_match": False,
                "role_match": False,
                "status": "MISSING_IN_NOTION",
            })

    # Unmatched Notion participants
    for idx, n_part in enumerate(notion_participants):
        if idx not in used_notion_indices:
            discrepancies.append(f"Notion participant '{n_part.get('name')}' ({n_part.get('role')}) was not found in the PDF report.")
            comparison.append({
                "pdf_name": None,
                "notion_name": n_part.get("name"),
                "pdf_role": None,
                "notion_role": n_part.get("role"),
                "pdf_dob": None,
                "notion_dob": n_part.get("dob"),
                "dob_match": False,
                "role_match": False,
                "status": "MISSING_IN_PDF",
            })

    return {
        "verified": len(discrepancies) == 0,
        "has_notion_records": True,
        "discrepancies": discrepancies,
        "comparison": comparison,
    }
