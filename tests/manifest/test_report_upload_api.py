import io
import pytest
from unittest.mock import MagicMock
from starlette.testclient import TestClient

from app import app
from auth import clear_rate_limits
from report_bot.domain import JobRecord, JobItem, ReportData, Participant, ItemState, JobState, CaseType, LabSource, ReviewMode


@pytest.fixture
def auth_client():
    clear_rate_limits()
    c = TestClient(app)
    c.post("/login", data={"username": "admin", "password": "manifest2026!"})
    return c


def test_upload_notion_reports_endpoint_unauthenticated():
    c = TestClient(app)
    res = c.post("/api/jobs/job-123/upload-notion-reports")
    assert res.status_code == 401


def test_upload_notion_reports_endpoint_no_items(auth_client):
    job_store = app.state.job_store
    job = JobRecord(
        lab_source=LabSource.EUROFINS,
        review_mode=ReviewMode.REVIEW,
        default_case_type=CaseType.PERSONAL,
        expires_at=job_store.new_expiry(),
    )
    job_store.create(job)

    # Calling upload-notion-reports with no complete items returns 400
    res = auth_client.post(f"/api/jobs/{job.id}/upload-notion-reports")
    assert res.status_code == 400
    data = res.json()
    assert data["success"] is False
    assert "No finalised reports" in data["message"]


def test_upload_notion_reports_endpoint_success(auth_client, monkeypatch, tmp_path):
    job_store = app.state.job_store
    job = JobRecord(
        lab_source=LabSource.EUROFINS,
        review_mode=ReviewMode.REVIEW,
        default_case_type=CaseType.PERSONAL,
        expires_at=job_store.new_expiry(),
    )
    job_store.create(job)

    # Create dummy completed item with output file
    out_file = tmp_path / "test_out.pdf"
    out_file.write_bytes(b"%PDF-1.4 dummy pdf content")

    item = JobItem(
        original_filename="source.pdf",
        stored_filename="stored.pdf",
        source_path=out_file,
        output_filename="CD-HOM-010126-01.pdf",
        output_path=out_file,
        case_type=CaseType.CERTIFIED_LEGAL,
        state=ItemState.COMPLETE,
        report=ReportData(
            case_code="CD-HOM-010126-01",
            collection_date="2026-01-01",
            test_type="PATERNITY",
            participants=[Participant(sample_number="S1", role="ALLEGED FATHER", full_name="JOHN DOE", date_of_birth="01/01/1980")],
        ),
    )
    job.items.append(item)
    job.state = JobState.COMPLETE
    job_store.save(job)

    # Mock upload_batch_reports_to_notion
    monkeypatch.setattr(
        "app.upload_batch_reports_to_notion",
        lambda items_data, **kwargs: {
            "total": 1,
            "uploaded": 1,
            "failed": 0,
            "results": [{"item_id": item.id, "success": True, "page_id": "mock-page-123"}],
        },
    )

    res = auth_client.post(f"/api/jobs/{job.id}/upload-notion-reports")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["uploaded"] == 1

    # Reload job and verify item status
    updated_job = job_store.get(job.id)
    assert updated_job.items[0].notion_upload_status == "UPLOADED"
    assert updated_job.items[0].notion_page_id == "mock-page-123"


def test_upload_single_item_notion_report_endpoint_success(auth_client, monkeypatch, tmp_path):
    job_store = app.state.job_store
    job = JobRecord(
        lab_source=LabSource.EUROFINS,
        review_mode=ReviewMode.REVIEW,
        default_case_type=CaseType.PERSONAL,
        expires_at=job_store.new_expiry(),
    )
    job_store.create(job)

    out_file = tmp_path / "test_out2.pdf"
    out_file.write_bytes(b"%PDF-1.4 dummy pdf content 2")

    item = JobItem(
        original_filename="source2.pdf",
        stored_filename="stored2.pdf",
        source_path=out_file,
        output_filename="SS-HOM-010126-02.pdf",
        output_path=out_file,
        case_type=CaseType.PERSONAL,
        state=ItemState.COMPLETE,
        report=ReportData(
            case_code="SS-HOM-010126-02",
            collection_date="2026-01-01",
            test_type="PATERNITY",
            participants=[Participant(sample_number="S2", role="ALLEGED FATHER", full_name="BOB SMITH", date_of_birth="01/01/1980")],
        ),
    )
    job.items.append(item)
    job_store.save(job)

    monkeypatch.setattr(
        "app.upload_single_case_report",
        lambda case_code, **kwargs: {
            "success": True,
            "case_code": case_code,
            "page_id": "page-ss-123",
        },
    )

    res = auth_client.post(f"/api/jobs/{job.id}/items/{item.id}/upload-notion-report")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["page_id"] == "page-ss-123"

    updated_job = job_store.get(job.id)
    assert updated_job.items[0].notion_upload_status == "UPLOADED"
    assert updated_job.items[0].notion_page_id == "page-ss-123"


def test_approve_endpoint_blocks_issue_case_type(auth_client, tmp_path):
    job_store = app.state.job_store
    job = JobRecord(
        lab_source=LabSource.EUROFINS,
        review_mode=ReviewMode.REVIEW,
        default_case_type=CaseType.ISSUE,
        expires_at=job_store.new_expiry(),
    )
    job_store.create(job)

    out_file = tmp_path / "test_issue.pdf"
    out_file.write_bytes(b"%PDF-1.4 dummy pdf")

    item = JobItem(
        original_filename="source.pdf",
        stored_filename="stored.pdf",
        source_path=out_file,
        case_type=CaseType.ISSUE,
        state=ItemState.NEEDS_REVIEW,
        report=ReportData(
            case_code="UNKNOWN-101026-01",
            collection_date="2026-10-10",
            test_type="PATERNITY",
            participants=[
                Participant(sample_number="S1", role="ALLEGED FATHER", full_name="JOHN DOE", date_of_birth="01/01/1980"),
                Participant(sample_number="S2", role="CHILD", full_name="JANE DOE", date_of_birth="02/02/2015"),
            ],
        ),
    )
    job.items.append(item)
    job_store.save(job)

    res = auth_client.post(f"/api/jobs/{job.id}/items/{item.id}/approve")
    assert res.status_code == 422
    assert "Report type must be set to either Personal or Certified / Legal" in res.json()["detail"]


def test_approve_endpoint_allows_personal_case_type(auth_client, tmp_path):
    job_store = app.state.job_store
    job = JobRecord(
        lab_source=LabSource.EUROFINS,
        review_mode=ReviewMode.REVIEW,
        default_case_type=CaseType.PERSONAL,
        expires_at=job_store.new_expiry(),
    )
    job_store.create(job)

    out_file = tmp_path / "test_personal.pdf"
    out_file.write_bytes(b"%PDF-1.4 dummy pdf")

    item = JobItem(
        original_filename="source.pdf",
        stored_filename="stored.pdf",
        source_path=out_file,
        case_type=CaseType.PERSONAL,
        state=ItemState.NEEDS_REVIEW,
        report=ReportData(
            case_code="PP-ACC-101026-01",
            collection_date="2026-10-10",
            test_type="PATERNITY",
            participants=[
                Participant(sample_number="S1", role="ALLEGED FATHER", full_name="JOHN DOE", date_of_birth="01/01/1980"),
                Participant(sample_number="S2", role="CHILD", full_name="JANE DOE", date_of_birth="02/02/2015"),
            ],
        ),
    )
    job.items.append(item)
    job_store.save(job)

    res = auth_client.post(f"/api/jobs/{job.id}/items/{item.id}/approve")
    assert res.status_code == 200
    assert res.json()["approved"] is True

