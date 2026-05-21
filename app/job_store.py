"""In-memory job registry for asynchronous PPT generation.

The feedback-loop path in ``/generate_ppt`` routinely runs 60-180s
(PPTX render → LibreOffice PNG → VLM call, ×N iterations). That blows
past Dify's HTTP node timeout (~60s), the client gives up and retries,
and the retries queue up at FastAPI producing a stream of identical
runs in ``outputs/runs/``.

The fix is to return a ``job_id`` immediately and let the client poll.
This module owns the in-memory job table that backs that flow.

Jobs are intentionally process-local: they vanish on restart. Finished
jobs auto-expire after ``ttl_seconds`` so a long-running process does
not accumulate them forever.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class Job:
    job_id: str
    status: str
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


TERMINAL_STATUSES = {"completed", "failed"}


class JobStore:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._lock = threading.Lock()
        self._jobs: Dict[str, Job] = {}
        self._events: Dict[str, threading.Event] = {}
        self._ttl = ttl_seconds

    def create(self) -> Job:
        job = Job(job_id=uuid.uuid4().hex[:16], status="pending", created_at=now_iso())
        with self._lock:
            self._jobs[job.job_id] = job
            self._events[job.job_id] = threading.Event()
            self._gc_locked()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            self._gc_locked()
            return self._jobs.get(job_id)

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in fields.items():
                setattr(job, key, value)
            if job.status in TERMINAL_STATUSES:
                event = self._events.get(job_id)
                if event is not None:
                    event.set()

    def wait_for_terminal(self, job_id: str, timeout: float) -> Optional[Job]:
        """Block until the job reaches a terminal state or timeout elapses.

        Returns the current Job snapshot afterwards (may still be running
        if timeout expired). Returns None if the job_id is unknown.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status in TERMINAL_STATUSES:
                return job
            event = self._events.get(job_id)

        if event is not None:
            event.wait(timeout=timeout)

        return self.get(job_id)

    def _gc_locked(self) -> None:
        cutoff = time.time() - self._ttl
        doomed: list[str] = []
        for jid, job in self._jobs.items():
            if job.status not in TERMINAL_STATUSES:
                continue
            ts = job.finished_at or job.created_at
            try:
                if datetime.fromisoformat(ts).timestamp() < cutoff:
                    doomed.append(jid)
            except Exception:
                continue
        for jid in doomed:
            self._jobs.pop(jid, None)
            self._events.pop(jid, None)


JOBS = JobStore()
