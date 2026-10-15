from datetime import date
from pathlib import Path

import fitz
import pytest

from report_bot.domain import CaseType, ItemState, JobItem, JobRecord, LabSource, ReviewMode
from report_bot.hongkong_parser import parse_hong_kong_report
from report_bot.parsing import parse_report
from report_bot.pipeline import ReportPipeline
from report_bot.store import JobStore

BASE_DIR = Path(__file__).resolve().parent.parent


def test_parse_hong_kong_prenatal_pnp_ebd():
    fixture_path = BASE_DIR / "PNP-EBD-250926-01.pdf"
    if not fixture_path.exists():
        pytest.skip("PNP-EBD-250926-01.pdf not found")
    pdf_bytes = fixture_path.read_bytes()
    report = parse_hong_kong_report(pdf_bytes, "PNP-EBD-250926-01.pdf")

    assert report.case_code == "PNP-EBD-250926-01"
    assert report.collection_date == date(2026, 9, 25)
    assert report.report_date == date(2026, 10, 5)
    assert report.test_type == "PRENATAL PATERNITY"
    assert report.confidence == 1.0
    assert not report.blocking_errors
    assert "One or more participants have no date of birth in the source report" in report.warnings
    assert not any("No child role" in w for w in report.warnings)

    assert len(report.participants) == 2
    mother, father = report.participants
    assert mother.sample_number == "PT71771W"
    assert mother.full_name == "CELESTINA AGGREY"
    assert mother.role == "MOTHER"

    assert father.sample_number == "PT71771H"
    assert father.full_name == "SYLVAN FAFA AMEKUGEE"
    assert father.role == "ALLEGED FATHER"


def test_parse_report_dispatch_hong_kong():
    fixture_path = BASE_DIR / "PNP-EBD-250926-01.pdf"
    if not fixture_path.exists():
        pytest.skip("PNP-EBD-250926-01.pdf not found")
    pdf_bytes = fixture_path.read_bytes()
    report = parse_report(LabSource.HONG_KONG, pdf_bytes, "PNP-EBD-250926-01.pdf")

    assert report.case_code == "PNP-EBD-250926-01"
    assert report.test_type == "PRENATAL PATERNITY"
    assert len(report.participants) == 2


def test_hong_kong_pipeline_encryption_and_naming(test_settings):
    fixture_path = BASE_DIR / "PNP-EBD-250926-01.pdf"
    if not fixture_path.exists():
        pytest.skip("PNP-EBD-250926-01.pdf not found")
    pdf_bytes = fixture_path.read_bytes()

    store = JobStore(test_settings.job_dir, test_settings.job_ttl_seconds)
    job = JobRecord(
        lab_source=LabSource.HONG_KONG,
        review_mode=ReviewMode.REVIEW,
        default_case_type=CaseType.PERSONAL,
        expires_at=store.new_expiry(),
    )
    store.create(job)

    stored = store.job_directory(job.id) / "source-0.pdf"
    stored.write_bytes(pdf_bytes)
    item = JobItem(
        original_filename="PNP-EBD-250926-01.pdf",
        stored_filename=stored.name,
        source_path=stored,
        case_type=CaseType.PERSONAL,
        state=ItemState.APPROVED,
        report=parse_hong_kong_report(pdf_bytes, "PNP-EBD-250926-01.pdf"),
        approved=True,
    )
    job.items.append(item)
    store.save(job)

    processed_job = ReportPipeline(test_settings).process_job(job.id, store)
    assert processed_job.items[0].state == ItemState.COMPLETE

    output_path = processed_job.items[0].output_path
    assert output_path and output_path.exists()
    assert output_path.name == "Amekugee-Blueprint-PNP-EBD-250926-01.pdf"

    # Verify original page count was preserved (no verification page attached, no modification)
    orig_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    orig_pages = len(orig_doc)
    orig_doc.close()

    # Verify encryption with derived password '124bdo09'
    enc_doc = fitz.open(output_path)
    assert enc_doc.is_encrypted
    auth_result = enc_doc.authenticate("124bdo09")
    assert auth_result > 0
    assert len(enc_doc) == orig_pages
    enc_doc.close()


def test_parse_hong_kong_gender_pnp_ebd():
    fixture_path = BASE_DIR / "PNP-EBD-290926-01-HK_report_sex_PT71863.pdf"
    if not fixture_path.exists():
        pytest.skip("PNP-EBD-290926-01-HK_report_sex_PT71863.pdf not found")
    pdf_bytes = fixture_path.read_bytes()
    report = parse_hong_kong_report(
        pdf_bytes, "PNP-EBD-290926-01-HK_report_sex_PT71863.pdf"
    )

    assert report.case_code == "PNP-EBD-290926-01"
    assert report.collection_date == date(2026, 9, 29)
    assert report.report_date == date(2026, 10, 9)
    assert report.test_type == "PRENATAL GENDER"
    assert report.confidence == 1.0
    assert not report.blocking_errors
    assert "One or more participants have no date of birth in the source report" in report.warnings

    assert len(report.participants) == 1
    mother = report.participants[0]
    assert mother.sample_number == "PT71863"
    assert mother.full_name == "THELMA QUARSHIE"
    assert mother.role == "MOTHER"


def test_hong_kong_pipeline_gender_report(test_settings):
    fixture_path = BASE_DIR / "PNP-EBD-290926-01-HK_report_sex_PT71863.pdf"
    if not fixture_path.exists():
        pytest.skip("PNP-EBD-290926-01-HK_report_sex_PT71863.pdf not found")
    pdf_bytes = fixture_path.read_bytes()

    store = JobStore(test_settings.job_dir, test_settings.job_ttl_seconds)
    job = JobRecord(
        lab_source=LabSource.HONG_KONG,
        review_mode=ReviewMode.REVIEW,
        default_case_type=CaseType.PERSONAL,
        expires_at=store.new_expiry(),
    )
    store.create(job)

    stored = store.job_directory(job.id) / "source-0.pdf"
    stored.write_bytes(pdf_bytes)
    item = JobItem(
        original_filename="PNP-EBD-290926-01-HK_report_sex_PT71863.pdf",
        stored_filename=stored.name,
        source_path=stored,
        case_type=CaseType.PERSONAL,
        state=ItemState.APPROVED,
        report=parse_hong_kong_report(
            pdf_bytes, "PNP-EBD-290926-01-HK_report_sex_PT71863.pdf"
        ),
        approved=True,
    )
    job.items.append(item)
    store.save(job)

    processed_job = ReportPipeline(test_settings).process_job(job.id, store)
    assert processed_job.items[0].state == ItemState.COMPLETE

    output_path = processed_job.items[0].output_path
    assert output_path and output_path.exists()
    assert output_path.name == "Quarshie-Blueprint-PNP-EBD-290926-01.pdf"

    # Verify original page count preserved (1 page, no verification pages or rebranding)
    enc_doc = fitz.open(output_path)
    assert enc_doc.is_encrypted
    auth_result = enc_doc.authenticate("124bdo09")
    assert auth_result > 0
    assert len(enc_doc) == 1
    enc_doc.close()


def test_parse_hong_kong_multi_alleged_fathers():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 100), "BLUEPRINT DNA & DIAGNOSTICS")
    page.insert_text((50, 130), "Non-Invasive Prenatal Paternity Test")
    page.insert_text((50, 160), "Collection Date: 25 September 2026")
    page.insert_text((50, 180), "Customer Name    Sample ID    Gender    Sample")
    page.insert_text((50, 210), "CELESTINA AGGREY   PT71771W   Female   Blood")
    page.insert_text((50, 230), "SYLVAN FAFA AMEKUGEE   PT71771H1   Male   Oral Swabs")
    page.insert_text((50, 250), "KOFI MENSAH   PT71771H2   Male   Oral Swabs")
    page.insert_text((50, 280), "Test ID: VGHK202609PT71771   Report Date: 05 October 2026")
    page.insert_text((50, 310), "Test Results: 1. ...")

    pdf_bytes = doc.tobytes()
    report = parse_hong_kong_report(pdf_bytes, "PNP-EBD-250926-01.pdf")
    assert report.case_code == "PNP-EBD-250926-01"
    assert report.test_type == "PRENATAL PATERNITY"
    assert not report.blocking_errors
    assert "Multiple participants match the primary tested adult role" in report.warnings
    assert len(report.participants) == 3

    mother, father1, father2 = report.participants
    assert mother.role == "MOTHER"
    assert mother.sample_number == "PT71771W"
    assert father1.role == "ALLEGED FATHER"
    assert father1.sample_number == "PT71771H1"
    assert father2.role == "ALLEGED FATHER"
    assert father2.sample_number == "PT71771H2"


def test_parse_hong_kong_multi_mothers():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 100), "BLUEPRINT DNA & DIAGNOSTICS")
    page.insert_text((50, 130), "Non-Invasive Prenatal Paternity Test")
    page.insert_text((50, 160), "Collection Date: 25 September 2026")
    page.insert_text((50, 180), "Customer Name    Sample ID    Gender    Sample")
    page.insert_text((50, 210), "CELESTINA AGGREY   PT71771W1   Female   Blood")
    page.insert_text((50, 230), "AMA SERWAA   PT71771W2   Female   Blood")
    page.insert_text((50, 250), "SYLVAN FAFA AMEKUGEE   PT71771H   Male   Oral Swabs")
    page.insert_text((50, 280), "Test ID: VGHK202609PT71771   Report Date: 05 October 2026")
    page.insert_text((50, 310), "Test Results: 1. ...")

    pdf_bytes = doc.tobytes()
    report = parse_hong_kong_report(pdf_bytes, "PNP-EBD-250926-01.pdf")
    assert report.case_code == "PNP-EBD-250926-01"
    assert report.test_type == "PRENATAL PATERNITY"
    assert not report.blocking_errors
    assert len(report.participants) == 3

    mother1, mother2, father = report.participants
    assert mother1.role == "MOTHER"
    assert mother1.sample_number == "PT71771W1"
    assert mother2.role == "MOTHER"
    assert mother2.sample_number == "PT71771W2"
    assert father.role == "ALLEGED FATHER"
    assert father.sample_number == "PT71771H"

