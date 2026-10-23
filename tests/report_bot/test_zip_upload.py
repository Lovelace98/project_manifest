from __future__ import annotations

import io
import zipfile
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from app import app
from auth import clear_rate_limits

ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def auth_client():
    clear_rate_limits()
    client = TestClient(app)
    client.post("/login", data={"username": "admin", "password": "manifest2026!"})
    return client


def _sample_pdf_bytes() -> bytes:
    sample_path = ROOT / "CD-FML-260926-01.pdf"
    if sample_path.exists():
        return sample_path.read_bytes()
    # Fallback to minimal valid PDF bytes
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\nxref\n0 4\n0000000000 65535 f \n0000000010 00000 n \n0000000060 00000 n \n0000000117 00000 n \ntrailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n190\n%%EOF"


def test_zip_upload_success(auth_client: TestClient) -> None:
    pdf_bytes = _sample_pdf_bytes()
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        z.writestr("report1.pdf", pdf_bytes)
    zip_buffer.seek(0)

    response = auth_client.post(
        "/api/jobs",
        data={"review_mode": "REVIEW", "default_case_type": "PERSONAL"},
        files=[("files", ("batch.zip", zip_buffer.getvalue(), "application/zip"))],
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["original_filename"] == "report1.pdf"


def test_zip_upload_with_skipped_non_pdfs(auth_client: TestClient) -> None:
    pdf_bytes = _sample_pdf_bytes()
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        z.writestr("report.pdf", pdf_bytes)
        z.writestr("notes.txt", b"Some laboratory notes")
        z.writestr("invoice.xlsx", b"Excel spreadsheet data")
    zip_buffer.seek(0)

    response = auth_client.post(
        "/api/jobs",
        data={"review_mode": "REVIEW", "default_case_type": "PERSONAL"},
        files=[("files", ("batch.zip", zip_buffer.getvalue(), "application/zip"))],
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert len(payload["items"]) == 1
    warnings = payload["items"][0].get("report", {}).get("warnings", [])
    assert any("Skipped non-PDF files from ZIP" in w for w in warnings)
    assert any("notes.txt" in w for w in warnings)
    assert any("invoice.xlsx" in w for w in warnings)


def test_zip_upload_nested_folders_and_macosx(auth_client: TestClient) -> None:
    pdf_bytes = _sample_pdf_bytes()
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        z.writestr("subfolder/deep/nested_report.pdf", pdf_bytes)
        z.writestr("__MACOSX/._nested_report.pdf", b"mac metadata")
        z.writestr(".DS_Store", b"ds store")
    zip_buffer.seek(0)

    response = auth_client.post(
        "/api/jobs",
        data={"review_mode": "REVIEW", "default_case_type": "PERSONAL"},
        files=[("files", ("mac_batch.zip", zip_buffer.getvalue(), "application/zip"))],
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["original_filename"] == "nested_report.pdf"


def test_zip_upload_exceeding_max_files(auth_client: TestClient) -> None:
    pdf_bytes = _sample_pdf_bytes()
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        for idx in range(26):
            z.writestr(f"report_{idx:02d}.pdf", pdf_bytes)
    zip_buffer.seek(0)

    response = auth_client.post(
        "/api/jobs",
        data={"review_mode": "REVIEW", "default_case_type": "PERSONAL"},
        files=[("files", ("too_many.zip", zip_buffer.getvalue(), "application/zip"))],
    )
    assert response.status_code == 400
    assert "exceeds the limit of 25" in response.json()["detail"]


def test_zip_upload_no_pdfs(auth_client: TestClient) -> None:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        z.writestr("document.docx", b"word doc")
        z.writestr("image.png", b"png image")
    zip_buffer.seek(0)

    response = auth_client.post(
        "/api/jobs",
        data={"review_mode": "REVIEW", "default_case_type": "PERSONAL"},
        files=[("files", ("no_pdfs.zip", zip_buffer.getvalue(), "application/zip"))],
    )
    assert response.status_code == 400
    assert "no readable PDF reports" in response.json()["detail"]


def test_mixed_zip_and_pdf_upload(auth_client: TestClient) -> None:
    pdf_bytes = _sample_pdf_bytes()
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        z.writestr("zipped_report.pdf", pdf_bytes)
    zip_buffer.seek(0)

    response = auth_client.post(
        "/api/jobs",
        data={"review_mode": "REVIEW", "default_case_type": "PERSONAL"},
        files=[
            ("files", ("loose_report.pdf", pdf_bytes, "application/pdf")),
            ("files", ("package.zip", zip_buffer.getvalue(), "application/zip")),
        ],
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert len(payload["items"]) == 2
    filenames = {item["original_filename"] for item in payload["items"]}
    assert "loose_report.pdf" in filenames
    assert "zipped_report.pdf" in filenames
