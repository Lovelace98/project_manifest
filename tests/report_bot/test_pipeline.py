from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pymupdf as fitz

from report_bot.domain import CaseType, ItemState, JobItem, JobRecord, JobState, ReviewMode
from report_bot.parser import parse_eurofins_report
from report_bot.pipeline import ReportPipeline
from report_bot.store import JobStore


class FakeConverter:
    def convert(self, _docx_path: Path, pdf_path: Path) -> Path:
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "VERIFICATION PAGE")
        document.save(pdf_path)
        document.close()
        return pdf_path


def test_mixed_pipeline_merge_encryption_partial_failure_and_private_summary(
    project_root: Path,
    test_settings,
) -> None:
    immutable_paths = [
        project_root / "blueprint_logo.png",
        project_root / "verification page template.docx",
        project_root / "flow" / "static" / "assets" / "Contact PNG.png",
    ]
    immutable_bytes = {path: path.read_bytes() for path in immutable_paths}
    store = JobStore(test_settings.job_dir, test_settings.job_ttl_seconds)
    job = JobRecord(
        review_mode=ReviewMode.REVIEW,
        default_case_type=CaseType.PERSONAL,
        expires_at=store.new_expiry(),
    )
    store.create(job)

    configurations = [
        ("CD-EBD-210926-01.pdf", CaseType.PERSONAL),
        ("CD-EBD-220926-01.pdf", CaseType.CERTIFIED_LEGAL),
    ]
    for index, (filename, case_type) in enumerate(configurations):
        source = project_root / filename
        stored = store.job_directory(job.id) / f"source-{index}.pdf"
        stored.write_bytes(source.read_bytes())
        job.items.append(
            JobItem(
                original_filename=filename,
                stored_filename=stored.name,
                source_path=stored,
                case_type=case_type,
                state=ItemState.APPROVED,
                report=parse_eurofins_report(source.read_bytes(), filename),
                approved=True,
            )
        )

    missing_source = store.job_directory(job.id) / "missing.pdf"
    failed_report_path = project_root / "CD-KBDO-220926-01.pdf"
    job.items.append(
        JobItem(
            original_filename=failed_report_path.name,
            stored_filename=missing_source.name,
            source_path=missing_source,
            case_type=CaseType.PERSONAL,
            state=ItemState.APPROVED,
            report=parse_eurofins_report(
                failed_report_path.read_bytes(),
                failed_report_path.name,
            ),
            approved=True,
        )
    )
    store.save(job)

    result = ReportPipeline(test_settings, converter=FakeConverter()).process_job(job.id, store)
    assert result.state == JobState.PARTIAL
    assert result.progress == 100
    assert [item.state for item in result.items] == [
        ItemState.COMPLETE,
        ItemState.COMPLETE,
        ItemState.FAILED,
    ]
    assert result.zip_path and result.zip_path.exists()

    expected_pages = [1, 2]
    for item, page_count in zip(result.items[:2], expected_pages, strict=True):
        assert item.output_path and item.output_path.exists()
        document = fitz.open(item.output_path)
        try:
            assert document.needs_pass
            password = f"124bdo{item.report.collection_date.month:02d}"
            assert document.authenticate(password)
            assert document.page_count == page_count
        finally:
            document.close()

    with ZipFile(result.zip_path) as archive:
        summary = archive.read("summary.csv").decode("utf-8")
        assert "item_number,case_code,case_type,status,warning_count" in summary
        assert "KENNEDY" not in summary
        assert "02/05/1974" not in summary
        assert "124bdo" not in summary
        assert configurations[0][0] not in summary
        assert len([name for name in archive.namelist() if name.endswith(".pdf")]) == 2

    for path, original in immutable_bytes.items():
        assert path.read_bytes() == original


def test_pipeline_multi_report_shared_case_code_numbering(
    project_root: Path,
    test_settings,
) -> None:
    source_pdf = project_root / "CD G34_0412-2.pdf"
    if not source_pdf.exists():
        import pytest
        pytest.skip("CD G34_0412-2.pdf not found")

    from report_bot.domain import LabSource
    from report_bot.ufc_parser import parse_ufc_report

    store = JobStore(test_settings.job_dir, test_settings.job_ttl_seconds)
    job = JobRecord(
        lab_source=LabSource.UFC,
        review_mode=ReviewMode.REVIEW,
        default_case_type=CaseType.CERTIFIED_LEGAL,
        expires_at=store.new_expiry(),
    )
    store.create(job)

    # Simulate two reports sharing the case code CD-EBD-260926-03
    for index in (1, 2):
        stored = store.job_directory(job.id) / f"source-{index}.pdf"
        stored.write_bytes(source_pdf.read_bytes())
        report = parse_ufc_report(source_pdf.read_bytes(), f"CD G34_0412-{index}.pdf")
        job.items.append(
            JobItem(
                original_filename=f"CD G34_0412-{index}.pdf",
                stored_filename=f"source-{index}.pdf",
                source_path=stored,
                case_type=CaseType.CERTIFIED_LEGAL,
                state=ItemState.APPROVED,
                report=report,
                approved=True,
            )
        )
    store.save(job)

    result = ReportPipeline(test_settings, converter=FakeConverter()).process_job(job.id, store)
    assert result.state == JobState.COMPLETE
    assert [item.output_filename for item in result.items] == [
        "Agyapong-Blueprint-CD-EBD-260926-03_1.pdf",
        "Agyapong-Blueprint-CD-EBD-260926-03_2.pdf",
    ]
    with ZipFile(result.zip_path) as archive:
        pdf_names = sorted(name for name in archive.namelist() if name.endswith(".pdf"))
        assert pdf_names == [
            "Agyapong-Blueprint-CD-EBD-260926-03_1.pdf",
            "Agyapong-Blueprint-CD-EBD-260926-03_2.pdf",
        ]
