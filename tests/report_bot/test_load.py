from __future__ import annotations

import resource
import sys
import time
from pathlib import Path
from zipfile import ZipFile

import pymupdf as fitz

from report_bot.domain import CaseType, ItemState, JobItem, JobRecord, ReviewMode
from report_bot.parser import parse_eurofins_report
from report_bot.pipeline import ReportPipeline
from report_bot.store import JobStore


def _case_variant(source_bytes: bytes, old_case: str, new_case: str) -> bytes:
    document = fitz.open(stream=source_bytes, filetype="pdf")
    page = document[0]
    hits = page.search_for(old_case)
    assert len(hits) == 1
    rectangle = hits[0]
    page.add_redact_annot(rectangle, fill=(1, 1, 1))
    page.apply_redactions()
    page.insert_text(
        (rectangle.x0, rectangle.y1 - 1),
        new_case,
        fontsize=10,
        fontname="hebo",
        color=(0, 0, 0),
    )
    payload = document.tobytes(garbage=4, deflate=True)
    document.close()
    return payload


def _resident_bytes() -> int:
    maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return maximum if sys.platform == "darwin" else maximum * 1024


def test_twenty_five_report_batch_completes_with_bounded_memory_and_isolation(
    project_root: Path,
    test_settings,
) -> None:
    source_path = project_root / "CD-EBD-210926-01.pdf"
    source_bytes = source_path.read_bytes()
    store = JobStore(test_settings.job_dir, test_settings.job_ttl_seconds)
    job = JobRecord(
        review_mode=ReviewMode.REVIEW,
        default_case_type=CaseType.PERSONAL,
        expires_at=store.new_expiry(),
    )
    store.create(job)

    expected_cases: set[str] = set()
    for index in range(1, 26):
        case_code = f"CD-LOAD-011026-{index:02d}"
        expected_cases.add(case_code)
        payload = _case_variant(source_bytes, "CD-EBD-210926-01", case_code)
        source = store.job_directory(job.id) / f"source-{index:02d}.pdf"
        source.write_bytes(payload)
        job.items.append(
            JobItem(
                original_filename=f"{case_code}.pdf",
                stored_filename=source.name,
                source_path=source,
                case_type=CaseType.PERSONAL,
                state=ItemState.APPROVED,
                report=parse_eurofins_report(payload, f"{case_code}.pdf"),
                approved=True,
            )
        )
    store.save(job)

    memory_before = _resident_bytes()
    started = time.monotonic()
    result = ReportPipeline(test_settings).process_job(job.id, store)
    elapsed = time.monotonic() - started
    memory_growth = max(0, _resident_bytes() - memory_before)

    assert result.state.value == "COMPLETE"
    assert len(result.items) == 25
    assert all(item.state == ItemState.COMPLETE for item in result.items)
    assert elapsed < 60
    assert memory_growth < 300 * 1024 * 1024
    assert result.zip_path
    with ZipFile(result.zip_path) as archive:
        pdf_names = [name for name in archive.namelist() if name.endswith(".pdf")]
        assert len(pdf_names) == 25
        summary = archive.read("summary.csv").decode("utf-8")
        assert all(case_code in summary for case_code in expected_cases)
        assert "CD-EBD-210926-01" not in summary

    second_job = JobRecord(
        review_mode=ReviewMode.REVIEW,
        default_case_type=CaseType.PERSONAL,
        expires_at=store.new_expiry(),
    )
    store.create(second_job)
    assert not any(store.job_directory(second_job.id).iterdir())
    assert all(
        item.output_path and store.job_directory(job.id) in item.output_path.parents
        for item in result.items
    )
