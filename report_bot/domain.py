from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from typing import Any
from pydantic import BaseModel, Field, field_validator


MISSING_DOB_PLACEHOLDERS = {
    "",
    "-",
    "--",
    "---",
    "----",
    "n/a",
    "na",
    "none",
    "unknown",
    "notstated",
    "notprovided",
    "notrecorded",
    "dobunknown",
    "--/--/----",
    "--.--.----",
    "--/--/--",
    "-/-/-",
    "-.-.-",
    "00/00/0000",
    "00.00.0000",
    "00-00-0000",
    "0/0/0000",
    "----/--/--",
    "//",
    "///",
}

DATE_RE = re.compile(r"^(?:\d{2}/\d{2}/\d{4}|--/--/\d{4}|-)$")


def normalize_dob_value(value: object) -> str:
    if value is None:
        return "-"
    raw = str(value).strip()
    cleaned = re.sub(r"\s+", "", raw).upper()
    if not cleaned or cleaned.lower() in MISSING_DOB_PLACEHOLDERS:
        return "-"

    standardized = cleaned.replace(".", "/")
    if standardized.lower() in MISSING_DOB_PLACEHOLDERS:
        return "-"

    if re.fullmatch(r"\d{1,2}-\d{1,2}-\d{4}", standardized):
        standardized = standardized.replace("-", "/")
    elif re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", standardized):
        standardized = standardized.replace("-", "/")

    if re.fullmatch(r"\d{4}", standardized):
        year = int(standardized)
        if 1900 <= year <= 2100:
            return f"--/--/{year}"

    m_year = re.fullmatch(
        r"(?:--|\?\?|XX|00|\d{2}|)/+(?:--|\?\?|XX|00|\d{2}|)/+(\d{4})", standardized
    )
    if m_year:
        year = int(m_year.group(1))
        parts = [p for p in standardized.split("/") if p]
        if (
            len(parts) == 3
            and parts[0].isdigit()
            and parts[1].isdigit()
            and int(parts[0]) > 0
            and int(parts[1]) > 0
        ):
            pass
        elif 1900 <= year <= 2100:
            return f"--/--/{year}"

    m_iso = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", standardized)
    if m_iso:
        y, m, d = m_iso.groups()
        return f"{int(d):02d}/{int(m):02d}/{y}"

    m_dmy = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", standardized)
    if m_dmy:
        d, m, y = m_dmy.groups()
        return f"{int(d):02d}/{int(m):02d}/{y}"

    return standardized[:10]


def valid_date_of_birth(value: str) -> bool:
    cleaned = normalize_dob_value(value)
    if cleaned == "-":
        return True
    if re.fullmatch(r"--/--/\d{4}", cleaned):
        try:
            year = int(cleaned[-4:])
            return 1900 <= year <= 2100
        except ValueError:
            return False
    if not DATE_RE.fullmatch(cleaned):
        return False
    try:
        datetime.strptime(cleaned, "%d/%m/%Y")
    except ValueError:
        return False
    return True


class CaseType(StrEnum):
    PERSONAL = "PERSONAL"
    CERTIFIED_LEGAL = "CERTIFIED_LEGAL"


class LabSource(StrEnum):
    EUROFINS = "EUROFINS"
    UFC = "UFC"
    HONG_KONG = "HONG_KONG"


class ReviewMode(StrEnum):
    REVIEW = "REVIEW"
    AUTO_APPROVE = "AUTO_APPROVE"


class JobState(StrEnum):
    UPLOADED = "UPLOADED"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    READY = "READY"
    PROCESSING = "PROCESSING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class ItemState(StrEnum):
    PARSED = "PARSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED = "APPROVED"
    PROCESSING = "PROCESSING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class Participant(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    sample_number: str = Field(min_length=1, max_length=64)
    role: str = Field(min_length=1, max_length=100)
    full_name: str = Field(min_length=1, max_length=200)
    date_of_birth: str = Field(default="-", max_length=10)
    source_order: int = Field(default=0, ge=0, le=59)

    @field_validator("sample_number", "role", "full_name", mode="before")
    @classmethod
    def uppercase_dynamic_text(cls, value: object) -> str:
        return str(value or "").strip().upper()

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def normalize_dob(cls, value: object) -> str:
        return normalize_dob_value(value)


class ReportData(BaseModel):
    case_code: str = Field(min_length=1, max_length=64)
    collection_date: date
    report_date: date | None = None
    test_type: str = Field(min_length=1, max_length=64)
    participants: list[Participant] = Field(min_length=1, max_length=60)
    warnings: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(default_factory=list)
    confidence: float = 1.0

    @field_validator("case_code", "test_type", mode="before")
    @classmethod
    def uppercase_fields(cls, value: object) -> str:
        return str(value or "").strip().upper()


class JobItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    original_filename: str
    stored_filename: str
    source_path: Path
    case_type: CaseType
    state: ItemState = ItemState.PARSED
    report: ReportData | None = None
    parse_error: str | None = None
    approved: bool = False
    output_filename: str | None = None
    output_path: Path | None = None
    processing_error: str | None = None
    layout_warnings: list[str] = Field(default_factory=list)
    classification_info: dict[str, Any] = Field(default_factory=dict)
    notion_verification: dict[str, Any] = Field(default_factory=dict)
    needs_manual_review: bool = False
    notion_page_id: str | None = None
    notion_upload_status: str | None = None
    notion_upload_error: str | None = None


class JobRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    lab_source: LabSource = LabSource.EUROFINS
    review_mode: ReviewMode
    default_case_type: CaseType
    auto_upload_notion: bool = False
    state: JobState = JobState.UPLOADED
    items: list[JobItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    progress: int = 0
    zip_path: Path | None = None

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)


class AppPreferences(BaseModel):
    default_review_mode: ReviewMode = ReviewMode.REVIEW
    default_case_type: CaseType = CaseType.PERSONAL


class ParticipantUpdate(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    sample_number: str = Field(min_length=1, max_length=64)
    role: str = Field(min_length=1, max_length=100)
    full_name: str = Field(min_length=1, max_length=200)
    date_of_birth: str = Field(default="-", max_length=10)
    source_order: int = Field(ge=0, le=59)

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def normalize_dob(cls, value: object) -> str:
        return normalize_dob_value(value)


class ItemUpdate(BaseModel):
    case_type: CaseType
    case_code: str = Field(min_length=1, max_length=64)
    test_type: str = Field(min_length=1, max_length=64)
    collection_date: date
    report_date: date | None = None
    participants: list[ParticipantUpdate] = Field(min_length=1, max_length=60)


def utc_now() -> datetime:
    return datetime.now(UTC)
