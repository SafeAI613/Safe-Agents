"""Background jobs (installs) whose progress events can be streamed to a client."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from .errors import ManagerError
from .installer import EventSink, InstallEvent


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class Job:
    kind: str
    id: str = field(default_factory=lambda: uuid4().hex)
    status: JobStatus = JobStatus.PENDING
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    _events: list[InstallEvent] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add_event(self, event: InstallEvent) -> None:
        with self._lock:
            self._events.append(event)

    def events_since(self, index: int) -> list[InstallEvent]:
        with self._lock:
            return self._events[index:]

    @property
    def done(self) -> bool:
        return self.status in (JobStatus.SUCCEEDED, JobStatus.FAILED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
        }


class JobRunner:
    """Runs jobs one at a time: installs share the uv cache and should not race each other."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, work: Callable[[EventSink], dict[str, Any]]) -> Job:
        job = Job(kind=kind)
        with self._lock:
            self._jobs[job.id] = job

        def run() -> None:
            job.status = JobStatus.RUNNING
            try:
                job.result = work(job.add_event)
                job.status = JobStatus.SUCCEEDED
            except ManagerError as exc:
                job.error = {"code": exc.code, "message": str(exc)}
                job.status = JobStatus.FAILED
            except Exception as exc:  # keep the worker thread alive whatever happens
                job.error = {"code": "internal_error", "message": str(exc)}
                job.status = JobStatus.FAILED

        self._executor.submit(run)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
