from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pymupdf as fitz

from report_bot.domain import (
    DATE_RE,
    Participant,
    ReportData,
    normalize_dob_value,
    valid_date_of_birth,
)
from report_bot.ordering import has_expected_primary_role, role_combination_warnings

CASE_RE = re.compile(
    r"\b([A-Z0-9]{2,8}(?:-[A-Z0-9]+)*-\d{6}-\d{2,4})\b",
    re.IGNORECASE,
)
SAMPLE_RE = re.compile(r"^[A-Z]{2,}\d{6}_\d+$", re.IGNORECASE)
KNOWN_TEST_TYPES = (
    ("GRANDPARENT", "GRANDPARENTAGE"),
    ("AVUNCULAR", "AVUNCULAR"),
    ("SIBLING", "SIBLINGSHIP"),
    ("Y-CHROMOSOME", "Y-CHROMOSOME"),
    ("Y CHROMOSOME", "Y-CHROMOSOME"),
    ("Y-STR", "Y-CHROMOSOME"),
    ("MITOCHONDRIAL", "MITOCHONDRIAL"),
    ("MTDNA", "MITOCHONDRIAL"),
    ("ZYGOSITY", "ZYGOSITY"),
    ("TWIN", "ZYGOSITY"),
    ("PATERNITY", "PATERNITY"),
    ("MATERNITY", "MATERNITY"),
)


class ReportParseError(ValueError):
    pass


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


def _page_words(page: fitz.Page) -> list[Word]:
    return [
        Word(float(row[0]), float(row[1]), float(row[2]), float(row[3]), str(row[4]))
        for row in page.get_text("words", sort=True)
        if float(row[0]) <= page.rect.width + 2
    ]


def _extract_case_code(text: str) -> str:
    ref_match = re.search(
        r"reference\s*(?:number)?\s*[:#\s]+([A-Z0-9]{2,8}(?:-[A-Z0-9]+)*-\d{6}-\d{2,4})",
        text,
        re.IGNORECASE,
    )
    if ref_match:
        return ref_match.group(1).upper()
    match = CASE_RE.search(text)
    if not match:
        raise ReportParseError("Case reference code was not found")
    return match.group(1).upper()


def _collection_date(case_code: str):
    parts = case_code.split("-")
    if len(parts) < 3:
        raise ReportParseError("Case reference does not contain a date segment")
    date_segment = parts[-2]
    if len(date_segment) != 6 or not date_segment.isdigit():
        raise ReportParseError("Case reference does not contain a valid 6-digit date segment")
    try:
        return datetime.strptime(date_segment, "%d%m%y").date()
    except ValueError as exc:
        raise ReportParseError("Case reference contains an invalid collection date") from exc


def _report_date(words: list[Word]):
    top_text = " ".join(word.text for word in words if word.y0 < 175)
    for candidate in re.findall(r"\b\d{2}/\d{2}/\d{4}\b", top_text):
        try:
            return datetime.strptime(candidate, "%d/%m/%Y").date()
        except ValueError:
            continue
    return None


def _find_header(words: list[Word]) -> tuple[Word, Word, Word, Word]:
    for sample in (word for word in words if word.text.upper() == "SAMPLE"):
        same_line = [word for word in words if abs(word.y0 - sample.y0) <= 3]
        role = next((word for word in same_line if word.text.upper() == "ROLE"), None)
        name = next((word for word in same_line if word.text.upper() == "NAME"), None)
        date = next((word for word in same_line if word.text.upper() == "DATE"), None)
        if role and name and date:
            return sample, role, name, date
    raise ReportParseError("Participant table header was not found")


def _group_rows(words: list[Word], start_y: float, end_y: float) -> list[list[Word]]:
    selected = sorted(
        (word for word in words if start_y < word.y0 < end_y),
        key=lambda word: (word.y0, word.x0),
    )
    groups: list[list[Word]] = []
    for word in selected:
        if not groups or abs(groups[-1][0].y0 - word.y0) > 2.2:
            groups.append([word])
        else:
            groups[-1].append(word)
    return groups


def _parse_participants(words: list[Word]) -> list[Participant]:
    sample_header, role_header, name_header, date_header = _find_header(words)
    dna_candidates = [
        word.y0
        for word in words
        if word.y0 > sample_header.y1 and word.text.upper() == "DNA"
    ]
    table_end = min(dna_candidates) if dna_candidates else float("inf")

    participants: list[Participant] = []
    for row in _group_rows(words, sample_header.y1, table_end):
        row = sorted(row, key=lambda word: word.x0)
        sample = " ".join(word.text for word in row if word.x0 < role_header.x0).strip()
        role = " ".join(
            word.text for word in row if role_header.x0 <= word.x0 < name_header.x0
        ).strip()
        name = " ".join(
            word.text for word in row if name_header.x0 <= word.x0 < date_header.x0
        ).strip()
        dob = " ".join(word.text for word in row if word.x0 >= date_header.x0).strip()
        normalized_dob = normalize_dob_value(dob)

        if SAMPLE_RE.fullmatch(sample) and role and name and valid_date_of_birth(normalized_dob):
            if len(sample) > 64 or len(role) > 100 or len(name) > 200:
                raise ReportParseError("Participant data exceeds the supported length")
            participants.append(
                Participant(
                    sample_number=sample,
                    role=role,
                    full_name=name,
                    date_of_birth=normalized_dob,
                    source_order=len(participants),
                )
            )
        elif participants and not sample and not role and not dob and name:
            last = participants[-1]
            existing_name = last.full_name.strip()
            continuation_name = name.strip()
            if existing_name.endswith("-"):
                merged_name = f"{existing_name}{continuation_name}"
            else:
                merged_name = f"{existing_name} {continuation_name}"

            if len(merged_name) > 200:
                raise ReportParseError("Participant data exceeds the supported length")

            participants[-1] = Participant(
                sample_number=last.sample_number,
                role=last.role,
                full_name=merged_name,
                date_of_birth=last.date_of_birth,
                source_order=last.source_order,
            )
    return participants


def _parse_document_participants(document: fitz.Document) -> list[Participant]:
    participants: list[Participant] = []
    seen_samples: set[str] = set()
    for page in document:
        try:
            page_participants = _parse_participants(_page_words(page))
        except ReportParseError as exc:
            if "header was not found" in str(exc):
                continue
            raise
        for participant in page_participants:
            if participant.sample_number in seen_samples:
                continue
            seen_samples.add(participant.sample_number)
            participant.source_order = len(participants)
            participants.append(participant)
    if len(participants) < 2:
        raise ReportParseError("At least two participant rows are required")
    if len(participants) > 60:
        raise ReportParseError("Report exceeds the 60-participant limit")
    return participants


def _infer_test_type(
    text: str,
    participants: list[Participant],
    first_page_words: list[Word] | None = None,
) -> tuple[str, bool]:
    roles = " ".join(participant.role.upper() for participant in participants)
    has_alleged_mother = "ALLEGED MOTHER" in roles or (
        "MOTHER" in roles and "ALLEGED" in roles and "FATHER" not in roles
    )
    has_alleged_father = "ALLEGED FATHER" in roles or ("FATHER" in roles and "ALLEGED" in roles)

    # 1. Prioritize explicit Page 1 certificate title header
    if first_page_words:
        title_candidates = [w for w in first_page_words if 110 <= w.y0 <= 200]
        title_line = " ".join(w.text.upper() for w in title_candidates)
        if "MATERNITY" in title_line:
            return "MATERNITY", True
        if "PATERNITY" in title_line and not (has_alleged_mother and not has_alleged_father):
            return "PATERNITY", True
        if "SIBLING" in title_line:
            return "SIBLINGSHIP", True
        if "AVUNCULAR" in title_line:
            return "AVUNCULAR", True
        if "GRANDPARENT" in title_line:
            return "GRANDPARENTAGE", True
        if (
            "Y-CHROMOSOME" in title_line
            or "Y CHROMOSOME" in title_line
            or "Y-STR" in title_line
        ):
            return "Y-CHROMOSOME", True
        if "MITOCHONDRIAL" in title_line or "MTDNA" in title_line:
            return "MITOCHONDRIAL", True
        if "ZYGOSITY" in title_line or "TWIN" in title_line:
            return "ZYGOSITY", True

    # 2. Strict participant role verification
    if has_alleged_mother and not has_alleged_father:
        return "MATERNITY", False
    if has_alleged_father and not has_alleged_mother:
        if "UNCLE" in roles or "AUNT" in roles:
            return "AVUNCULAR", False
        if "GRAND" in roles:
            return "GRANDPARENTAGE", False
        return "PATERNITY", False
    if "UNCLE" in roles or "AUNT" in roles:
        return "AVUNCULAR", False
    if "GRAND" in roles:
        return "GRANDPARENTAGE", False
    if "SIBLING" in roles:
        return "SIBLINGSHIP", False
    if "Y-CHROMOSOME" in roles or "Y-STR" in roles:
        return "Y-CHROMOSOME", False
    if "MITOCHONDRIAL" in roles or "MTDNA" in roles:
        return "MITOCHONDRIAL", False

    # 3. Document text scan with role safety
    upper = " ".join(text.upper().split())
    for token, normalized in KNOWN_TEST_TYPES:
        if token in upper:
            if normalized == "PATERNITY" and has_alleged_mother and not has_alleged_father:
                return "MATERNITY", False
            return normalized, True

    raise ReportParseError("Test type could not be inferred")


def parse_eurofins_report(
    pdf_bytes: bytes,
    filename: str,
    *,
    max_file_bytes: int = 25 * 1024 * 1024,
) -> ReportData:
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ReportParseError("File does not have a valid PDF header")
    if len(pdf_bytes) > max_file_bytes:
        raise ReportParseError("PDF exceeds the 25 MB file limit")

    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ReportParseError("PDF could not be opened") from exc

    try:
        if document.needs_pass:
            raise ReportParseError("Encrypted input PDFs are not supported")
        if document.page_count < 1:
            raise ReportParseError("PDF contains no pages")

        all_words: list[Word] = []
        page_texts: list[str] = []
        for page in document:
            words = _page_words(page)
            all_words.extend(words)
            page_texts.append(" ".join(word.text for word in words))
        full_text = " ".join(page_texts)
        if len(full_text.strip()) < 100:
            raise ReportParseError("PDF is image-only or has insufficient extractable text")

        case_code = _extract_case_code(full_text)
        filename_match = CASE_RE.search(Path(filename).stem.upper())
        blocking_errors: list[str] = []
        warnings: list[str] = []
        if filename_match and filename_match.group(1).upper() != case_code:
            blocking_errors.append("Filename case code does not match the report case code")

        first_page_words = _page_words(document[0])
        participants = _parse_document_participants(document)
        test_type, explicit_test_type = _infer_test_type(
            full_text, participants, first_page_words
        )

        if not explicit_test_type:
            warnings.append("Test type was inferred from participant roles")
        if not has_expected_primary_role(test_type, participants):
            blocking_errors.append("Expected primary tested adult role was not found")
        relationship_warnings = role_combination_warnings(test_type, participants)
        warnings.extend(relationship_warnings)
        if any(
            participant.date_of_birth == "-" or participant.date_of_birth.startswith("--/--/")
            for participant in participants
        ):
            warnings.append("One or more participants have no date of birth in the source report")

        collection_date = _collection_date(case_code)
        report_date = _report_date(first_page_words)
        confidence = 1.0
        if warnings:
            confidence -= 0.1
        if relationship_warnings:
            confidence -= 0.1
        if blocking_errors:
            confidence = min(confidence, 0.5)

        return ReportData(
            case_code=case_code,
            collection_date=collection_date,
            report_date=report_date,
            test_type=test_type,
            participants=participants,
            warnings=warnings,
            blocking_errors=blocking_errors,
            confidence=max(confidence, 0.0),
        )
    finally:
        document.close()
