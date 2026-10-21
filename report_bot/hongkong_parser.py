"""Hong Kong Laboratory (Prenatal) report parser."""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pymupdf as fitz

from report_bot.domain import Participant, ReportData
from report_bot.ordering import has_expected_primary_role, role_combination_warnings

CASE_RE = re.compile(
    r"\b([A-Z0-9]{2,8}(?:-[A-Z0-9]+)*-\d{6}-\d{2,4})\b",
    re.IGNORECASE,
)
TEST_ID_RE = re.compile(r"Test\s+ID:\s*([A-Za-z0-9_-]+)", re.IGNORECASE)
COLLECTION_DATE_RE = re.compile(
    r"Collection\s+Date:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})", re.IGNORECASE
)
REPORT_DATE_RE = re.compile(
    r"Report\s+Date:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})", re.IGNORECASE
)


class HongKongParseError(Exception):
    """Raised when a Hong Kong laboratory report cannot be parsed."""


def _extract_case_code(filename: str, text: str) -> str:
    # 1. Prefer Blueprint case code in filename
    stem = Path(filename).stem
    fn_match = CASE_RE.search(stem)
    if fn_match:
        return fn_match.group(1).strip().upper()

    # 2. Search document text for Blueprint case code
    text_match = CASE_RE.search(text)
    if text_match:
        return text_match.group(1).strip().upper()

    # 3. Fallback to lab internal Test ID
    test_id_match = TEST_ID_RE.search(text)
    if test_id_match:
        return test_id_match.group(1).strip().upper()

    raise HongKongParseError("Case reference or Test ID was not found")


def _extract_dates(case_code: str, text: str) -> tuple[date, date | None]:
    collection_date: date | None = None
    report_date: date | None = None

    col_match = COLLECTION_DATE_RE.search(text)
    if col_match:
        try:
            collection_date = datetime.strptime(
                col_match.group(1).strip(), "%d %B %Y"
            ).date()
        except ValueError:
            pass

    if collection_date is None:
        parts = case_code.split("-")
        if len(parts) >= 3 and len(parts[-2]) == 6 and parts[-2].isdigit():
            try:
                collection_date = datetime.strptime(parts[-2], "%d%m%y").date()
            except ValueError:
                pass

    if collection_date is None:
        collection_date = date.today()

    rep_match = REPORT_DATE_RE.search(text)
    if rep_match:
        try:
            report_date = datetime.strptime(
                rep_match.group(1).strip(), "%d %B %Y"
            ).date()
        except ValueError:
            pass

    return collection_date, report_date


def _extract_test_type(text: str) -> str:
    head_text = (
        text.split("Collection Date")[0].upper()
        if "Collection Date" in text
        else text.upper()
    )
    if any(
        k in head_text
        for k in ("FETAL SEX", "SEX CHROMOSOME", "FETAL GENDER", "GENDER")
    ):
        return "PRENATAL GENDER"
    if "MATERNITY" in head_text:
        return "PRENATAL MATERNITY"
    if "PATERNITY" in head_text:
        return "PRENATAL PATERNITY"

    text_upper = text.upper()
    if any(k in text_upper for k in ("FETAL SEX", "SEX CHROMOSOME", "FETAL GENDER")):
        return "PRENATAL GENDER"
    if "MATERNITY" in text_upper:
        return "PRENATAL MATERNITY"
    return "PRENATAL PATERNITY"


def _parse_participants(page: fitz.Page, test_type: str) -> list[Participant]:
    words = page.get_text("words")
    # Locate Customer Name header row
    cust_words = [w for w in words if w[4].upper() == "CUSTOMER"]
    if not cust_words:
        raise HongKongParseError("Participant table header could not be located")
    header_bottom = max(w[3] for w in cust_words)

    # Find bottom boundary below header
    end_ys: list[float] = []
    for w in words:
        if w[1] > header_bottom:
            txt = w[4].upper()
            if txt in {"1.", "RESULTS:", "RESULTS"} or (txt == "TEST" and w[0] < 120):
                end_ys.append(w[1])
    table_end = min(end_ys) if end_ys else header_bottom + 300

    row_words = [w for w in words if header_bottom < w[1] < table_end and w[0] < 550]
    if not row_words:
        raise HongKongParseError("Participant table rows could not be located")

    lines: list[list[tuple]] = []
    for w in sorted(row_words, key=lambda x: (x[1], x[0])):
        if not lines or abs(w[1] - lines[-1][0][1]) > 5.0:
            lines.append([w])
        else:
            lines[-1].append(w)

    participants: list[Participant] = []
    for index, lwords in enumerate(lines):
        tokens = [w[4].strip() for w in lwords if w[4].strip()]
        if not tokens:
            continue

        # A valid participant line must explicitly contain FEMALE or MALE
        gender_idx = -1
        for i, t in enumerate(tokens):
            if t.upper() in {"FEMALE", "MALE"}:
                gender_idx = i
                break

        if gender_idx == -1:
            continue

        if gender_idx > 1:
            name = " ".join(tokens[: gender_idx - 1]).strip()
            sample_number = tokens[gender_idx - 1].strip()
            gender = tokens[gender_idx].upper()
        else:
            name = tokens[0].strip()
            sample_number = tokens[1].strip() if len(tokens) > 1 else ""
            gender = tokens[gender_idx].upper()

        if not name or not sample_number:
            continue

        if test_type == "PRENATAL MATERNITY":
            if gender == "FEMALE":
                role = "ALLEGED MOTHER"
            elif gender == "MALE":
                role = "FATHER"
            else:
                role = "PARTICIPANT"
        else:
            if gender == "FEMALE":
                role = "MOTHER"
            elif gender == "MALE":
                role = "ALLEGED FATHER"
            else:
                role = "PARTICIPANT"

        participants.append(
            Participant(
                sample_number=sample_number,
                role=role,
                full_name=name,
                date_of_birth="-",
                source_order=len(participants),
            )
        )

    is_single = any(t in test_type.upper() for t in ("GENDER", "SEX", "PROFILE", "PERSONAL"))
    min_expected = 1 if is_single else 2
    if len(participants) < min_expected:
        raise HongKongParseError(
            f"Found {len(participants)} participants; expected at least {min_expected}"
        )

    return participants


def parse_hong_kong_report(
    pdf_bytes: bytes,
    filename: str,
    *,
    max_file_bytes: int = 25 * 1024 * 1024,
) -> ReportData:
    """Extract case data from a Hong Kong Laboratory prenatal report PDF."""
    if len(pdf_bytes) > max_file_bytes:
        raise HongKongParseError("PDF file size exceeds the allowed limit")
    if not pdf_bytes.startswith(b"%PDF"):
        raise HongKongParseError("The uploaded file is not a valid PDF")

    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise HongKongParseError("The PDF document could not be opened") from exc

    try:
        if document.is_encrypted:
            raise HongKongParseError("Encrypted PDFs are not supported")
        if document.page_count < 1:
            raise HongKongParseError("The PDF document contains no pages")

        page = document[0]
        text = page.get_text()
        if not text.strip():
            raise HongKongParseError("The PDF does not contain extractable text")

        case_code = _extract_case_code(filename, text)
        collection_date, report_date = _extract_dates(case_code, text)
        test_type = _extract_test_type(text)
        participants = _parse_participants(page, test_type)

        warnings: list[str] = [
            "One or more participants have no date of birth in the source report"
        ]
        warnings.extend(role_combination_warnings(test_type, participants))

        blocking_errors: list[str] = []
        if not has_expected_primary_role(test_type, participants):
            blocking_errors.append("Expected primary tested adult role was not found")

        return ReportData(
            case_code=case_code,
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
