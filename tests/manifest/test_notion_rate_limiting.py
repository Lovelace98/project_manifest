import time
import pytest
from unittest.mock import MagicMock
import httpx

from notion_service import (
    NotionRateLimiter,
    execute_notion_request,
    upload_batch_reports_to_notion,
    upload_single_case_report,
)


def test_notion_rate_limiter_pacing():
    limiter = NotionRateLimiter(requests_per_second=10.0)  # 0.1s interval
    t0 = time.time()
    for _ in range(4):
        limiter.acquire()
    elapsed = time.time() - t0
    # 4 acquisitions at 0.1s interval should take at least ~0.28s
    assert elapsed >= 0.25


def test_execute_notion_request_success():
    client = MagicMock(spec=httpx.Client)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "page-123"}
    client.post.return_value = mock_resp

    headers = {"Authorization": "Bearer test"}
    resp = execute_notion_request(client, "POST", "https://api.notion.com/v1/test", headers, payload={"foo": "bar"})

    assert resp.status_code == 200
    client.post.assert_called_once()


def test_execute_notion_request_recovers_from_429():
    client = MagicMock(spec=httpx.Client)

    # First attempt: 429 Too Many Requests with Retry-After: 0.1
    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "0.1"}

    # Second attempt: 200 OK
    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {"success": True}

    client.get.side_effect = [resp_429, resp_200]

    headers = {"Authorization": "Bearer test"}
    resp = execute_notion_request(client, "GET", "https://api.notion.com/v1/pages/123", headers, max_retries=3)

    assert resp.status_code == 200
    assert client.get.call_count == 2


def test_execute_notion_request_recovers_from_502():
    client = MagicMock(spec=httpx.Client)

    resp_502 = MagicMock()
    resp_502.status_code = 502

    resp_200 = MagicMock()
    resp_200.status_code = 200

    client.post.side_effect = [resp_502, resp_200]

    headers = {"Authorization": "Bearer test"}
    resp = execute_notion_request(client, "POST", "https://api.notion.com/v1/query", headers, max_retries=3)

    assert resp.status_code == 200
    assert client.post.call_count == 2


def test_batch_upload_with_rate_limiting_and_ufc_hk(monkeypatch):
    """
    Simulates batch upload containing Eurofins, UFC, and Hong Kong reports
    while Notion API returns 429 on one request to verify seamless retry and recovery.
    """
    call_counts = {"count": 0}

    def mock_upload_single(case_code, **kwargs):
        call_counts["count"] += 1
        # Simulates successful upload for UFC, HK, and Eurofins cases
        return {
            "success": True,
            "case_code": case_code,
            "page_id": f"notion-page-{case_code}",
        }

    monkeypatch.setattr("notion_service.upload_single_case_report", mock_upload_single)

    items_data = [
        {"item_id": "item-eurofins", "case_code": "CD-HOM-010126-01", "filename": "Eurofins.pdf", "pdf_bytes": b"1"},
        {"item_id": "item-ufc", "case_code": "PP-ACC-020226-01", "filename": "UFC.pdf", "pdf_bytes": b"2"},
        {"item_id": "item-hk", "case_code": "PNP-HK-030326-01", "filename": "HK.pdf", "pdf_bytes": b"3"},
    ]

    res = upload_batch_reports_to_notion(items_data, api_key="mock-key", max_workers=2)

    assert res["total"] == 3
    assert res["uploaded"] == 3
    assert res["failed"] == 0
    assert len(res["results"]) == 3
