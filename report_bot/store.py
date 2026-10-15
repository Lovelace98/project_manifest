from __future__ import annotations

import shutil
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from report_bot.domain import JobRecord, JobState


class JobNotFoundError(KeyError):
    pass


class JobStore:
    def __init__(self, root: Path, ttl_seconds: int):
        self.root = root
        self.ttl_seconds = ttl_seconds
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)

    def new_expiry(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=self.ttl_seconds)

    def create(self, job: JobRecord) -> JobRecord:
        UUID(job.id)
        with self._lock:
            job_dir = self.root / job.id
            job_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
            self._jobs[job.id] = job
            return job

    def job_directory(self, job_id: str) -> Path:
        UUID(job_id)
        return self.root / job_id

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.state == JobState.EXPIRED:
                raise JobNotFoundError(job_id)
            if job.expires_at <= datetime.now(UTC):
                self._jobs.pop(job_id)
                job.state = JobState.EXPIRED
                shutil.rmtree(self.root / job_id, ignore_errors=True)
                raise JobNotFoundError(job_id)
            return job

    def save(self, job: JobRecord) -> JobRecord:
        with self._lock:
            if job.id not in self._jobs:
                raise JobNotFoundError(job.id)
            job.touch()
            self._jobs[job.id] = job
            return job

    def cleanup_expired(self) -> int:
        now = datetime.now(UTC)
        removed = 0
        with self._lock:
            expired_ids = [
                job_id for job_id, job in self._jobs.items() if job.expires_at <= now
            ]
            for job_id in expired_ids:
                job = self._jobs.pop(job_id)
                job.state = JobState.EXPIRED
                shutil.rmtree(self.root / job_id, ignore_errors=True)
                removed += 1

            known = set(self._jobs)
            for child in self.root.iterdir():
                if child.is_dir() and child.name not in known:
                    try:
                        modified = datetime.fromtimestamp(child.stat().st_mtime, UTC)
                    except OSError:
                        continue
                    if modified + timedelta(seconds=self.ttl_seconds) <= now:
                        shutil.rmtree(child, ignore_errors=True)
                        removed += 1
        return removed

    def purge_orphans(self) -> int:
        removed = 0
        with self._lock:
            known = set(self._jobs)
            for child in self.root.iterdir():
                if child.is_dir() and child.name not in known:
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
        return removed
