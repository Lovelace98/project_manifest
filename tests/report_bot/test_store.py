from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from report_bot.domain import CaseType, JobRecord, ReviewMode
from report_bot.store import JobNotFoundError, JobStore


def test_expired_job_is_deleted_immediately_on_access(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs", ttl_seconds=3600)
    job = JobRecord(
        review_mode=ReviewMode.REVIEW,
        default_case_type=CaseType.PERSONAL,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    store.create(job)
    job_directory = store.job_directory(job.id)
    (job_directory / "private-report.pdf").write_bytes(b"private")

    with pytest.raises(JobNotFoundError):
        store.get(job.id)
    assert not job_directory.exists()


def test_startup_orphan_purge_removes_all_unknown_directories(tmp_path) -> None:
    root = tmp_path / "jobs"
    store = JobStore(root, ttl_seconds=3600)
    orphan = root / "orphan-from-previous-process"
    orphan.mkdir()
    (orphan / "report.pdf").write_bytes(b"private")

    assert store.purge_orphans() == 1
    assert not orphan.exists()
