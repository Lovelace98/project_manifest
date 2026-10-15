from __future__ import annotations

import io
import re
import time
from pathlib import Path

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from report_bot.main import create_app


def _login(client: TestClient, password: str = "unit-test-password") -> str:
    page = client.get("/login")
    token_match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token_match
    response = client.post(
        "/login",
        data={"password": password, "csrf_token": token_match.group(1)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie

    workspace = client.get("/")
    token_match = re.search(r'name="csrf-token" content="([^"]+)"', workspace.text)
    assert token_match
    return token_match.group(1)


def _upload(
    client: TestClient,
    csrf: str,
    paths: list[tuple[str, Path]],
    *,
    mode: str = "REVIEW",
) -> dict:
    files = [
        ("files", (upload_name, path.read_bytes(), "application/pdf"))
        for upload_name, path in paths
    ]
    response = client.post(
        "/api/jobs",
        data={"review_mode": mode, "default_case_type": "PERSONAL"},
        files=files,
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _wait_for_terminal(client: TestClient, job_id: str, timeout: float = 8) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["state"] not in {"READY", "PROCESSING"}:
            return payload
        time.sleep(0.05)
    raise AssertionError("Report job did not reach a terminal state")


def test_authentication_csrf_review_process_and_download(
    project_root: Path,
    test_settings,
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        assert client.get("/api/settings").status_code == 401
        csrf = _login(client)
        assert client.post("/logout").status_code == 403

        settings_response = client.put(
            "/api/settings",
            json={"default_review_mode": "AUTO_APPROVE"},
            headers={"X-CSRF-Token": csrf},
        )
        assert settings_response.status_code == 200
        assert settings_response.json()["default_review_mode"] == "AUTO_APPROVE"

        fixture = project_root / "CD-EBD-210926-01.pdf"
        if not fixture.exists():
            pytest.skip("CD-EBD-210926-01.pdf not found")
        job = _upload(client, csrf, [(fixture.name, fixture)])
        assert job["state"] == "AWAITING_REVIEW"
        assert job["items"][0]["state"] == "NEEDS_REVIEW"
        item_id = job["items"][0]["id"]


        report = job["items"][0]["report"]
        participants = report["participants"]
        for participant in participants:
            participant["sample_number"] = participant["sample_number"].lower()
            participant["role"] = participant["role"].lower()
            participant["full_name"] = participant["full_name"].lower()
        correction = client.patch(
            f"/api/jobs/{job['id']}/items/{item_id}",
            json={
                "case_type": "PERSONAL",
                "case_code": report["case_code"].lower(),
                "test_type": report["test_type"].lower(),
                "collection_date": report["collection_date"],
                "report_date": report["report_date"],
                "participants": participants,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert correction.status_code == 200, correction.text
        corrected = correction.json()["report"]
        assert corrected["case_code"] == corrected["case_code"].upper()
        assert corrected["test_type"] == corrected["test_type"].upper()
        assert all(
            participant["sample_number"] == participant["sample_number"].upper()
            and participant["role"] == participant["role"].upper()
            and participant["full_name"] == participant["full_name"].upper()
            for participant in corrected["participants"]
        )

        assert client.post(f"/api/jobs/{job['id']}/items/{item_id}/approve").status_code == 403
        approval = client.post(
            f"/api/jobs/{job['id']}/items/{item_id}/approve",
            headers={"X-CSRF-Token": csrf},
        )
        assert approval.status_code == 200
        assert approval.json()["approved"] is True

        start = client.post(
            f"/api/jobs/{job['id']}/process",
            headers={"X-CSRF-Token": csrf},
        )
        assert start.status_code == 202
        result = _wait_for_terminal(client, job["id"])
        assert result["state"] == "COMPLETE"
        assert result["download_ready"] is True

        download = client.get(f"/api/jobs/{job['id']}/download")
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/zip"
        assert download.content.startswith(b"PK")


def test_auto_approve_quarantines_mismatch_but_completes_clean_file(
    project_root: Path,
    test_settings,
) -> None:
    mismatch_source = project_root / "CD-EBD-210926-01.pdf"
    clean_source = project_root / "CD-EBD-220926-01.pdf"
    if not mismatch_source.exists() or not clean_source.exists():
        pytest.skip("Eurofins fixtures not found")
    app = create_app(test_settings)
    with TestClient(app) as client:
        csrf = _login(client)
        job = _upload(
            client,
            csrf,
            [
                ("CD-KBDO-230926-03.pdf", mismatch_source),
                (clean_source.name, clean_source),
            ],
            mode="AUTO_APPROVE",
        )
        result = _wait_for_terminal(client, job["id"])
        assert result["state"] == "PARTIAL"
        assert [item["state"] for item in result["items"]] == [
            "NEEDS_REVIEW",
            "COMPLETE",
        ]
        assert result["download_ready"] is True


def test_batch_limit_is_enforced_before_file_processing(project_root: Path, test_settings) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        csrf = _login(client)
        fixture = project_root / "CD-EBD-210926-01.pdf"
        if not fixture.exists():
            fixture = project_root / "CD G34_0412-2.pdf"
        files = [
            ("files", (f"report-{index}.pdf", fixture.read_bytes(), "application/pdf"))
            for index in range(26)
        ]
        response = client.post(
            "/api/jobs",
            data={"review_mode": "REVIEW", "default_case_type": "PERSONAL"},
            files=files,
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 400
        assert "1 and 25" in response.json()["detail"]


def test_mixed_per_file_case_types_are_kept_before_review(
    project_root: Path,
    test_settings,
) -> None:
    paths = [
        project_root / "CD-EBD-210926-01.pdf",
        project_root / "CD-EBD-220926-01.pdf",
    ]
    if not all(p.exists() for p in paths):
        pytest.skip("Eurofins fixtures not found")
    app = create_app(test_settings)
    with TestClient(app) as client:
        csrf = _login(client)
        response = client.post(
            "/api/jobs",
            data={
                "review_mode": "REVIEW",
                "default_case_type": "PERSONAL",
                "case_types": ["PERSONAL", "CERTIFIED_LEGAL"],
            },
            files=[
                ("files", (path.name, path.read_bytes(), "application/pdf"))
                for path in paths
            ],
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 201, response.text
        assert [item["case_type"] for item in response.json()["items"]] == [
            "PERSONAL",
            "CERTIFIED_LEGAL",
        ]


def test_duplicate_case_references_quarantine_both_reports(
    project_root: Path,
    test_settings,
) -> None:
    source = project_root / "CD-EBD-210926-01.pdf"
    if not source.exists():
        source = project_root / "CD G34_0412-2.pdf"
    document = fitz.open(source)
    metadata = document.metadata
    metadata["producer"] = "duplicate-case-test"
    document.set_metadata(metadata)
    variant = document.tobytes(garbage=4, deflate=True)
    document.close()

    app = create_app(test_settings)
    with TestClient(app) as client:
        csrf = _login(client)
        response = client.post(
            "/api/jobs",
            data={
                "lab_source": "UFC" if "G34" in source.name else "EUROFINS",
                "review_mode": "AUTO_APPROVE",
                "default_case_type": "PERSONAL",
            },
            files=[
                ("files", (source.name, source.read_bytes(), "application/pdf")),
                ("files", (source.name, variant, "application/pdf")),
            ],
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 201, response.text
        job = response.json()
        assert job["state"] == "AWAITING_REVIEW"
        assert [item["state"] for item in job["items"]] == [
            "NEEDS_REVIEW",
            "NEEDS_REVIEW",
        ]
        assert all(
            any(
                "Shared case reference" in warning
                for warning in item["report"]["warnings"]
            )
            for item in job["items"]
        )
        # Verify approval succeeds on shared case references
        item1_id = job["items"][0]["id"]
        approve_resp = client.post(
            f"/api/jobs/{job['id']}/items/{item1_id}/approve",
            headers={"X-CSRF-Token": csrf},
        )
        assert approve_resp.status_code == 200


def test_brand_logo_route_serves_the_cleaned_transparent_logo(
    test_settings,
    assert_no_opaque_near_white,
) -> None:
    app = create_app(test_settings)
    with TestClient(app) as client:
        response = client.get("/brand/logo")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.mode == "RGBA"
            assert_no_opaque_near_white(image)


def test_upload_and_process_ufc_personal(test_settings) -> None:
    from report_bot.pdf_service import PERSONAL_WARNING
    ufc_fixture = Path("G34_0408-1.pdf")
    if not ufc_fixture.exists():
        ufc_fixture = Path("CD G34_0412-2.pdf")
    app = create_app(test_settings)
    with TestClient(app) as client:
        csrf = _login(client)
        response = client.post(
            "/api/jobs",
            data={
                "lab_source": "UFC",
                "review_mode": "REVIEW",
                "default_case_type": "PERSONAL",
            },
            files=[("files", (ufc_fixture.name, ufc_fixture.read_bytes(), "application/pdf"))],
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 201, response.text
        job = response.json()
        assert job["lab_source"] == "UFC"
        assert len(job["items"]) == 1
        item = job["items"][0]
        assert item["case_type"] == "PERSONAL"

        # Approve item
        approve_resp = client.post(
            f"/api/jobs/{job['id']}/items/{item['id']}/approve",
            headers={"X-CSRF-Token": csrf},
        )
        assert approve_resp.status_code == 200

        # Process job
        process_resp = client.post(
            f"/api/jobs/{job['id']}/process",
            headers={"X-CSRF-Token": csrf},
        )
        assert process_resp.status_code == 202

        terminal = _wait_for_terminal(client, job["id"])
        assert terminal["state"] == "COMPLETE"
        assert terminal["items"][0]["state"] == "COMPLETE"
        assert terminal["download_ready"] is True

        download = client.get(f"/api/jobs/{job['id']}/download")
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/zip"

        import zipfile
        archive = zipfile.ZipFile(io.BytesIO(download.content))
        pdf_names = [n for n in archive.namelist() if n.endswith(".pdf")]
        assert len(pdf_names) == 1
        pdf_bytes = archive.read(pdf_names[0])
        # Verify AES encryption and decryption with 124bdo09
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        assert doc.is_encrypted
        doc.authenticate("124bdo09")
        page = doc[0]
        assert page.search_for(PERSONAL_WARNING)
        assert page.search_for("info@blueprintdna.org")
        assert not page.search_for("Testing performed by Universal Forensics")


class FakeConverter:
    def convert(self, _docx_path: Path, pdf_path: Path) -> Path:
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "VERIFICATION PAGE")
        document.save(pdf_path)
        document.close()
        return pdf_path


def test_upload_and_process_ufc_certified_legal(
    test_settings,
) -> None:
    from report_bot.pdf_service import PERSONAL_WARNING
    ufc_fixture = Path("G34_0408-1.pdf")
    if not ufc_fixture.exists():
        ufc_fixture = Path("CD G34_0412-2.pdf")
    app = create_app(test_settings)
    app.state.pipeline.converter = FakeConverter()
    with TestClient(app) as client:
        csrf = _login(client)
        response = client.post(
            "/api/jobs",
            data={
                "lab_source": "UFC",
                "review_mode": "REVIEW",
                "default_case_type": "CERTIFIED_LEGAL",
            },
            files=[("files", (ufc_fixture.name, ufc_fixture.read_bytes(), "application/pdf"))],
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 201
        job = response.json()
        item = job["items"][0]
        assert item["case_type"] == "CERTIFIED_LEGAL"

        client.post(
            f"/api/jobs/{job['id']}/items/{item['id']}/approve",
            headers={"X-CSRF-Token": csrf},
        )
        client.post(
            f"/api/jobs/{job['id']}/process",
            headers={"X-CSRF-Token": csrf},
        )

        terminal = _wait_for_terminal(client, job["id"])
        assert terminal["state"] == "COMPLETE"

        download = client.get(f"/api/jobs/{job['id']}/download")
        import zipfile
        archive = zipfile.ZipFile(io.BytesIO(download.content))
        pdf_names = [n for n in archive.namelist() if n.endswith(".pdf")]
        pdf_bytes = archive.read(pdf_names[0])

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        assert doc.is_encrypted
        doc.authenticate("124bdo09")
        # Should have verification page prepended (page 0 is verification, page 1 is report)
        assert doc.page_count == 2
        # Report page is page 1:
        report_page = doc[1]
        assert not report_page.search_for(PERSONAL_WARNING)
        assert not report_page.search_for("No Chain of Custody")
        assert report_page.search_for("Chain of Custody")
        assert report_page.search_for("info@blueprintdna.org")


def test_web_flow_hong_kong_prenatal(
    test_settings,
) -> None:
    hk_fixture = Path("PNP-EBD-250926-01.pdf")
    if not hk_fixture.exists():
        return
    app = create_app(test_settings)
    app.state.pipeline.converter = FakeConverter()
    with TestClient(app) as client:
        csrf = _login(client)
        response = client.post(
            "/api/jobs",
            data={
                "lab_source": "HONG_KONG",
                "review_mode": "REVIEW",
                "default_case_type": "PERSONAL",
            },
            files=[("files", (hk_fixture.name, hk_fixture.read_bytes(), "application/pdf"))],
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 201
        job = response.json()
        item = job["items"][0]
        assert item["report"]["case_code"] == "PNP-EBD-250926-01"
        assert item["report"]["test_type"] == "PRENATAL PATERNITY"
        assert not item["report"]["blocking_errors"]

        client.post(
            f"/api/jobs/{job['id']}/items/{item['id']}/approve",
            headers={"X-CSRF-Token": csrf},
        )
        client.post(
            f"/api/jobs/{job['id']}/process",
            headers={"X-CSRF-Token": csrf},
        )

        terminal = _wait_for_terminal(client, job["id"])
        assert terminal["state"] == "COMPLETE"

        download = client.get(f"/api/jobs/{job['id']}/download")
        import zipfile
        archive = zipfile.ZipFile(io.BytesIO(download.content))
        pdf_names = [n for n in archive.namelist() if n.endswith(".pdf")]
        assert len(pdf_names) == 1
        assert pdf_names[0] == "Amekugee-Blueprint-PNP-EBD-250926-01.pdf"
        pdf_bytes = archive.read(pdf_names[0])

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        assert doc.is_encrypted
        assert doc.authenticate("124bdo09") > 0
        # No verification page added, original pages preserved (3 pages)
        orig_doc = fitz.open(hk_fixture)
        assert doc.page_count == len(orig_doc)
        orig_doc.close()
        doc.close()



