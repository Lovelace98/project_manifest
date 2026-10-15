from __future__ import annotations

import csv
import io
import logging
import zipfile
from pathlib import Path

from report_bot.config import Settings
from report_bot.domain import CaseType, ItemState, JobRecord, JobState, LabSource
from report_bot.pdf_service import (
    detect_report_font_family,
    encrypt_pdf,
    merge_pdf_bytes,
    output_filename,
    password_for_case,
    rebrand_pdf,
    rebrand_ufc_pdf,
)
from report_bot.store import JobStore
from report_bot.verification import LibreOfficeConverter, VerificationError, build_verification_pdfs

logger = logging.getLogger(__name__)


class ReportPipeline:
    def __init__(
        self,
        settings: Settings,
        *,
        converter: LibreOfficeConverter | None = None,
    ):
        self.settings = settings
        self.converter = converter or LibreOfficeConverter()

    def _process_item(
        self,
        job: JobRecord,
        item_index: int,
        store: JobStore,
        *,
        suffix: str | int | None = None,
    ) -> bool:
        item = job.items[item_index]
        if not item.approved or not item.report:
            return False

        item.state = ItemState.PROCESSING
        store.save(job)
        try:
            source_bytes = item.source_path.read_bytes()
            if job.lab_source == LabSource.UFC:
                edited_report = rebrand_ufc_pdf(
                    source_bytes,
                    case_type=item.case_type,
                    test_type=item.report.test_type,
                    logo_path=self.settings.logo_path,
                )
            elif job.lab_source == LabSource.HONG_KONG:
                edited_report = source_bytes
            else:
                edited_report = rebrand_pdf(
                    source_bytes,
                    case_type=item.case_type,
                    test_type=item.report.test_type,
                    logo_path=self.settings.logo_path,
                )

            merge_inputs: list[bytes] = []
            work_dir = store.job_directory(job.id) / f"item-{item.id}"
            work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            if item.case_type == CaseType.CERTIFIED_LEGAL and job.lab_source != LabSource.HONG_KONG:
                verification_paths = build_verification_pdfs(
                    template_path=self.settings.verification_template_path,
                    work_dir=work_dir,
                    case_code=item.report.case_code,
                    collection_date=item.report.collection_date,
                    test_type=item.report.test_type,
                    participants=item.report.participants,
                    converter=self.converter,
                    font_family=detect_report_font_family(source_bytes),
                )
                merge_inputs.extend(path.read_bytes() for path in verification_paths)
            merge_inputs.append(edited_report)

            merged = merge_pdf_bytes(merge_inputs)
            user_password = password_for_case(
                item.report.case_code,
                "124bdo",
            )
            encrypted = encrypt_pdf(
                merged,
                user_password=user_password,
                owner_password=self.settings.pdf_owner_password,
            )
            filename = output_filename(
                item.report.case_code,
                item.report.test_type,
                item.report.participants,
                suffix=suffix,
            )
            outputs_dir = store.job_directory(job.id) / "outputs"
            outputs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            output_path = outputs_dir / filename
            collision_index = 1
            while output_path.exists():
                collision_index += 1
                stem = filename.removesuffix(".pdf")
                filename = f"{stem}_{collision_index}.pdf"
                output_path = outputs_dir / filename

            output_path.write_bytes(encrypted)
            output_path.chmod(0o600)

            item.output_filename = filename
            item.output_path = output_path
            item.state = ItemState.COMPLETE
            item.processing_error = None
            return True
        except VerificationError as exc:
            logger.exception(
                "Report verification conversion failed",
                extra={"job_id": job.id, "item_id": item.id},
            )
            item.state = ItemState.FAILED
            item.processing_error = (
                f"Conversion service error: {exc}. "
                "Ensure LibreOffice is installed or run the application inside Docker."
            )
            return False
        except Exception:
            logger.exception(
                "Report item processing failed",
                extra={"job_id": job.id, "item_id": item.id},
            )
            item.state = ItemState.FAILED
            item.processing_error = (
                "Report processing failed. Verify the source format and conversion service."
            )
            return False
        finally:
            store.save(job)

    def _create_zip(self, job: JobRecord, store: JobStore) -> Path | None:
        completed = [
            item
            for item in job.items
            if item.state == ItemState.COMPLETE and item.output_path
        ]
        if not completed:
            return None

        summary_stream = io.StringIO()
        writer = csv.writer(summary_stream)
        writer.writerow(
            [
                "item_number",
                "case_code",
                "case_type",
                "status",
                "warning_count",
            ]
        )
        for item_number, item in enumerate(job.items, start=1):
            writer.writerow(
                [
                    item_number,
                    item.report.case_code if item.report else "",
                    item.case_type.value,
                    item.state.value,
                    len(item.report.warnings) if item.report else 0,
                ]
            )

        zip_path = store.job_directory(job.id) / "Blueprint-Reports.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in completed:
                archive.write(item.output_path, arcname=item.output_filename)
            archive.writestr("summary.csv", summary_stream.getvalue())
        zip_path.chmod(0o600)
        return zip_path

    def process_job(self, job_id: str, store: JobStore) -> JobRecord:
        job = store.get(job_id)
        job.state = JobState.PROCESSING
        job.progress = 0
        store.save(job)

        eligible_indexes = [
            index for index, item in enumerate(job.items) if item.approved and item.report
        ]
        case_item_indexes: dict[str, list[int]] = {}
        for index in eligible_indexes:
            item = job.items[index]
            if item.report:
                case_item_indexes.setdefault(item.report.case_code, []).append(index)

        item_suffixes: dict[int, str | None] = {}
        for _case_code, indexes in case_item_indexes.items():
            if len(indexes) > 1:
                for order, idx in enumerate(indexes, start=1):
                    item_suffixes[idx] = str(order)
            else:
                item_suffixes[indexes[0]] = None

        total = len(eligible_indexes)
        successes = 0
        for position, item_index in enumerate(eligible_indexes, start=1):
            suffix = item_suffixes.get(item_index)
            if self._process_item(job, item_index, store, suffix=suffix):
                successes += 1
            job.progress = int(position / max(total, 1) * 100)
            store.save(job)

        job.zip_path = self._create_zip(job, store)
        failures = sum(item.state == ItemState.FAILED for item in job.items)
        quarantined = sum(item.state == ItemState.NEEDS_REVIEW for item in job.items)
        if successes == 0:
            job.state = JobState.FAILED
        elif failures or quarantined or successes < len(job.items):
            job.state = JobState.PARTIAL
        else:
            job.state = JobState.COMPLETE
        job.progress = 100
        return store.save(job)
