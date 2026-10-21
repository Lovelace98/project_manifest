"""UFC (Universal Forensics Corporation) report parser."""
from __future__ import annotations

import re
from datetime import date, datetime

import pymupdf as fitz

from report_bot.domain import Participant, ReportData
from report_bot.ordering import has_expected_primary_role, role_combination_warnings

ORDER_ID_RE = re.compile(
    r"Order\s+ID:\s*([A-Z0-9]+(?:-[A-Z0-9]+)*-\d{6}-\d{2,4})",
    re.IGNORECASE,
)
SIGNATURE_DATE_RE = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})"
)
RELATIONSHIP_KEYWORDS = {
    "ALLEGED",
    "FATHER",
    "MOTHER",
    "CHILD",
    "SIBLING",
    "SON",
    "DAUGHTER",
    "UNCLE",
    "AUNT",
    "GRANDMOTHER",
    "GRANDFATHER",
    "GRANDPARENT",
    "GRANDCHILD",
    "GRANDSON",
    "GRANDDAUGHTER",
    "RELATIVE",
    "MALE",
    "FEMALE",
    "NIECE",
    "NEPHEW",
    "COUSIN",
    "HALF",
    "FULL",
    "TWIN",
    "DONOR",
    "PARTICIPANT",
}
SAMPLE_TYPE_KEYWORDS = {"MOUTH", "SWAB", "BUCCAL", "BLOOD", "SALIVA"}


class UFCParseError(Exception):
    """Raised when a UFC report cannot be parsed."""


def _extract_order_id(page: fitz.Page, text: str, filename: str = "") -> str:
    match = ORDER_ID_RE.search(text)
    if match:
        return match.group(1).strip().upper()

    # Fallback 1: search text words in the Order ID region (bottom-up x:51, y:154)
    words = page.get_text("words")
    candidate_words = [
        w for w in words if 605 <= w[1] <= 650 and w[0] < 220
    ]
    if candidate_words:
        sorted_words = sorted(candidate_words, key=lambda x: (x[1], x[0]))
        line_text = " ".join(w[4] for w in sorted_words)
        match = ORDER_ID_RE.search(line_text)
        if match:
            return match.group(1).strip().upper()

    # Fallback 2: Search anywhere on the page for a Blueprint Case Code pattern
    bp_match = re.search(r"\b([A-Z0-9]+(?:-[A-Z0-9]+)*-\d{6}-\d{2,4})\b", text)
    if bp_match:
        return bp_match.group(1).strip().upper()

    # Fallback 3: Check filename for a Blueprint Case Code pattern
    if filename:
        fn_match = re.search(r"([A-Z0-9]+(?:-[A-Z0-9]+)*-\d{6}-\d{2,4})", filename.upper())
        if fn_match:
            return fn_match.group(1).strip().upper()

    # Fallback 4: Extract UFC Report ID (e.g. G34_0412-2) or filename stem so file can still be parsed and quarantined
    report_id_match = re.search(r"Report\s+ID:\s*([A-Z0-9_]+-\d+)", text, re.IGNORECASE)
    if report_id_match:
        return report_id_match.group(1).strip().upper()

    if filename:
        stem = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE).strip().upper()
        if stem:
            return stem

    raise UFCParseError("Could not find Blueprint Order ID in UFC report")


def _extract_report_date(text: str) -> date | None:
    match = SIGNATURE_DATE_RE.search(text)
    if match:
        try:
            return datetime.strptime(match.group(1), "%B %d, %Y").date()
        except ValueError:
            pass
    return None


def _infer_test_type(participants: list[Participant], text: str) -> str:
    roles = [p.role.upper() for p in participants]
    text_upper = text.upper()

    # Y-Chromosome / Y-STR testing
    if (
        "Y-CHROMOSOME" in text_upper
        or "Y CHROMOSOME" in text_upper
        or "Y-STR" in text_upper
        or "MALE LINE" in text_upper
        or "PATERNAL LINE" in text_upper
        or "HAPLOTYPE" in text_upper
        or any("MALE RELATIVE" in r for r in roles)
    ):
        return "Y-CHROMOSOME"

    # Mitochondrial testing
    if (
        "MITOCHONDRIAL" in text_upper
        or "MTDNA" in text_upper
        or "MATERNAL LINE" in text_upper
        or any("FEMALE RELATIVE" in r for r in roles)
    ):
        return "MITOCHONDRIAL"

    # Zygosity / Twin testing
    if (
        "ZYGOSITY" in text_upper
        or "TWIN" in text_upper
        or any("TWIN" in r for r in roles)
    ):
        return "ZYGOSITY"

    if any("SIBLING" in r for r in roles) or "SIBLING" in text_upper:
        return "SIBLINGSHIP"
    if any(k in r for r in roles for k in ("UNCLE", "AUNT", "AVUNCULAR")):
        return "AVUNCULAR"
    if any("GRAND" in r for r in roles):
        return "GRANDPARENTAGE"
    if any("MOTHER" in r for r in roles) and not any("FATHER" in r for r in roles):
        return "MATERNITY"
    if any("FATHER" in r for r in roles) or "PATERNITY" in text_upper:
        return "PATERNITY"

    return "PATERNITY"


def _parse_participants(page: fitz.Page) -> tuple[list[Participant], list[date]]:
    words = page.get_text("words")
    # Table rows are below the header (y >= 125) and above conclusions/data (y <= 185)
    row_words = [w for w in words if 125 <= w[1] <= 185 and w[0] < 450]
    if not row_words:
        raise UFCParseError("Participant table rows could not be located")

    lines: dict[float, list[tuple]] = {}
    for w in sorted(row_words, key=lambda x: (x[1], x[0])):
        line_key = round(w[1] / 6.0) * 6.0
        lines.setdefault(line_key, []).append(w)

    participants: list[Participant] = []
    collected_dates: list[date] = []

    for index, (_line_key, lwords) in enumerate(sorted(lines.items())):
        tokens = [w[4].strip() for w in lwords if w[4].strip()]
        if len(tokens) < 4:
            continue

        # Token 0: Date Collected
        date_str = tokens[0]
        try:
            c_date = datetime.strptime(date_str, "%m/%d/%Y").date()
            collected_dates.append(c_date)
        except ValueError:
            pass

        # Token 1: Case ID (used as sample_number)
        sample_number = tokens[1]

        rem_tokens = tokens[2:]
        # Locate relationship keyword start
        rel_idx = -1
        for i, t in enumerate(rem_tokens):
            if t.upper() in RELATIONSHIP_KEYWORDS:
                rel_idx = i
                break
        if rel_idx == -1:
            row_str = " ".join(tokens)
            raise UFCParseError(f"Could not determine relationship from row: {row_str}")

        name = " ".join(rem_tokens[:rel_idx]).strip()

        # Locate sample type keyword start (e.g., Mouth Swab)
        sample_idx = -1
        for i in range(rel_idx, len(rem_tokens)):
            if rem_tokens[i].upper() in SAMPLE_TYPE_KEYWORDS:
                sample_idx = i
                break

        if sample_idx == -1:
            role = " ".join(rem_tokens[rel_idx:]).strip()
        else:
            role = " ".join(rem_tokens[rel_idx:sample_idx]).strip()

        participants.append(
            Participant(
                sample_number=sample_number,
                role=role,
                full_name=name,
                date_of_birth="-",
                source_order=index,
            )
        )

    if len(participants) < 1:
        raise UFCParseError("Found 0 participants; expected at least 1")

    return participants, collected_dates


def parse_ufc_report(
    pdf_bytes: bytes,
    filename: str,
    *,
    max_file_bytes: int = 25 * 1024 * 1024,
) -> ReportData:
    """Extract case data from a UFC DNA Test Report PDF."""
    if len(pdf_bytes) > max_file_bytes:
        raise UFCParseError("PDF file size exceeds the allowed limit")
    if not pdf_bytes.startswith(b"%PDF"):
        raise UFCParseError("The uploaded file is not a valid PDF")

    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise UFCParseError("The PDF document could not be opened") from exc

    try:
        if document.is_encrypted:
            raise UFCParseError("Encrypted PDFs are not supported")
        if document.page_count < 1:
            raise UFCParseError("The PDF document contains no pages")

        page = document[0]
        text = page.get_text()
        if not text.strip():
            raise UFCParseError("The PDF does not contain extractable text")

        order_id = _extract_order_id(page, text, filename)
        report_date = _extract_report_date(text)
        participants, collected_dates = _parse_participants(page)
        test_type = _infer_test_type(participants, text)

        min_expected = 1 if any(t in test_type.upper() for t in ("GENDER", "SEX", "PROFILE", "PERSONAL")) else 2
        if len(participants) < min_expected:
            raise UFCParseError(f"Found {len(participants)} participants; expected at least {min_expected}")

        # Derive collection date from Order ID date segment (DDMMYY)
        # or fallback to collected dates from table
        parts = order_id.split("-")
        collection_date: date | None = None
        if len(parts) >= 3 and len(parts[-2]) == 6 and parts[-2].isdigit():
            try:
                collection_date = datetime.strptime(parts[-2], "%d%m%y").date()
            except ValueError:
                pass

        if collection_date is None:
            if collected_dates:
                collection_date = collected_dates[0]
            else:
                collection_date = date.today()

        warnings: list[str] = [
            "One or more participants have no date of birth in the source report"
        ]
        warnings.extend(role_combination_warnings(test_type, participants))

        blocking_errors: list[str] = []
        if not has_expected_primary_role(test_type, participants):
            blocking_errors.append("Expected primary tested adult role was not found")

        return ReportData(
            case_code=order_id,
            collection_date=collection_date,
            report_date=report_date,
            test_type=test_type,
            participants=participants,
            warnings=warnings,
            blocking_errors=blocking_errors,
            confidence=1.0,
        )
    finally:
        document.close()
