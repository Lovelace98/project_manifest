import os
import pytest
from unittest.mock import MagicMock
import httpx

from notion_service import (
    CUSTODY_PICS_PROPERTY,
    initiate_notion_file_upload,
    send_notion_file_bytes,
    attach_report_to_case_page,
    upload_single_case_report,
    upload_batch_reports_to_notion,
)


def test_initiate_notion_file_upload(monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": "upload-xyz-123",
        "upload_url": "https://api.notion.com/v1/file_uploads/upload-xyz-123/send",
    }

    client = MagicMock(spec=httpx.Client)
    client.post.return_value = mock_resp

    headers = {"Authorization": "Bearer test_token"}
    fid, url = initiate_notion_file_upload(client, headers, "CD-HOM-010126-01.pdf")

    assert fid == "upload-xyz-123"
    assert url == "https://api.notion.com/v1/file_uploads/upload-xyz-123/send"
    client.post.assert_called_once()
    assert client.post.call_args[1]["json"]["filename"] == "CD-HOM-010126-01.pdf"


def test_send_notion_file_bytes():
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    client = MagicMock(spec=httpx.Client)
    client.post.return_value = mock_resp

    headers = {"Authorization": "Bearer test_token", "Notion-Version": "2022-06-28"}
    send_notion_file_bytes(client, headers, "https://api.notion.com/upload", b"%PDF-1.5 test content", "test.pdf")

    client.post.assert_called_once()
    assert "file" in client.post.call_args[1]["files"]


def test_attach_report_preserves_existing_files():
    # Existing page contains 2 consent form photos
    page_data = {
        "id": "case-page-456",
        "properties": {
            "Custody Pics ": {
                "files": [
                    {
                        "name": "Consent_Form_P1.jpg",
                        "type": "file",
                        "file": {"url": "https://s3.amazonaws.com/consent1.jpg"},
                    },
                    {
                        "name": "ID_Card.png",
                        "type": "external",
                        "external": {"url": "https://example.com/id.png"},
                    },
                    {
                        "name": "Report-Final.pdf",  # previous version of report
                        "type": "file_upload",
                        "file_upload": {"id": "old-upload-id"},
                    },
                ]
            },
            "Status": {
                "type": "select",
                "select": {"name": "Dispatched to Laboratory"},
            },
        },
    }

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.json.return_value = page_data

    patch_resp = MagicMock()
    patch_resp.status_code = 200
    patch_resp.json.return_value = {"id": "case-page-456"}

    client = MagicMock(spec=httpx.Client)
    client.get.return_value = get_resp
    client.patch.return_value = patch_resp

    headers = {"Authorization": "Bearer test_token"}
    res = attach_report_to_case_page(
        client=client,
        headers=headers,
        page_id="case-page-456",
        file_upload_id="new-upload-id-789",
        filename="Report-Final.pdf",
        update_status=True,
        target_status="Result Review",
    )

    assert res["success"] is True
    # The old Report-Final.pdf should be replaced by new-upload-id-789,
    # and the 2 consent forms should be preserved! Total files = 3
    assert res["total_files"] == 3

    # Check patched properties payload
    patch_payload = client.patch.call_args[1]["json"]["properties"]
    patched_files = patch_payload["Custody Pics "]["files"]
    assert len(patched_files) == 3
    assert patched_files[0]["name"] == "Consent_Form_P1.jpg"
    assert patched_files[1]["name"] == "ID_Card.png"
    assert patched_files[2]["name"] == "Report-Final.pdf"
    assert patched_files[2]["file_upload"]["id"] == "new-upload-id-789"

    # Status updated to Result Review
    assert patch_payload["Status"]["select"]["name"] == "Result Review"


def test_upload_single_case_report_mocked(monkeypatch):
    import httpx

    # Mock find_case_page_by_code
    monkeypatch.setattr("notion_service.find_case_page_by_code", lambda *args, **kwargs: "resolved-page-999")
    monkeypatch.setattr("notion_service.initiate_notion_file_upload", lambda *args, **kwargs: ("f-up-1", "http://upload"))
    monkeypatch.setattr("notion_service.send_notion_file_bytes", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "notion_service.attach_report_to_case_page",
        lambda *args, **kwargs: {"success": True, "total_files": 2},
    )

    res = upload_single_case_report(
        case_code="CD-HOM-010126-01",
        pdf_bytes=b"%PDF-sample",
        filename="CD-HOM-010126-01.pdf",
        api_key="mock-key",
    )

    assert res["success"] is True
    assert res["case_code"] == "CD-HOM-010126-01"
    assert res["page_id"] == "resolved-page-999"


def test_upload_batch_reports_to_notion(monkeypatch):
    monkeypatch.setattr(
        "notion_service.upload_single_case_report",
        lambda case_code, **kwargs: {
            "success": True,
            "case_code": case_code,
            "page_id": f"page-{case_code}",
        },
    )

    items_data = [
        {"item_id": "item-1", "case_code": "SS-HOM-010126-01", "filename": "1.pdf", "pdf_bytes": b"1"},
        {"item_id": "item-2", "case_code": "CD-HOM-010126-02", "filename": "2.pdf", "pdf_bytes": b"2"},
    ]

    res = upload_batch_reports_to_notion(items_data, api_key="mock-key")
    assert res["total"] == 2
    assert res["uploaded"] == 2
    assert res["failed"] == 0
    assert len(res["results"]) == 2
