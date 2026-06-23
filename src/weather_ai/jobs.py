from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Callable
import uuid


JobCallable = Callable[[], Any]


@dataclass
class JobRecord:
    id: str
    name: str
    status: str = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: Any = None
    error: str | None = None
    logs: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = Lock()

    def start(self, name: str, func: JobCallable) -> JobRecord:
        job = JobRecord(id=uuid.uuid4().hex[:12], name=name)
        self._append(job, f"{name}: geplant.")
        with self._lock:
            self._jobs[job.id] = job
        thread = Thread(target=self._run, args=(job.id, func), daemon=True)
        thread.start()
        return self.get(job.id)

    def list(self) -> list[JobRecord]:
        with self._lock:
            jobs = list(self._jobs.values())
        return sorted(jobs, key=lambda item: item.created_at, reverse=True)

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            job = self._jobs[job_id]
            return _copy_job(job)

    def _run(self, job_id: str, func: JobCallable) -> None:
        self._update(job_id, status="running", started_at=datetime.now(timezone.utc))
        self._append_id(job_id, "Ausfuehrung gestartet.")
        try:
            result = func()
        except Exception as exc:  # noqa: BLE001 - jobs should capture failures for the UI.
            self._update(
                job_id,
                status="failed",
                completed_at=datetime.now(timezone.utc),
                error=str(exc),
            )
            self._append_id(job_id, f"Fehler: {exc}")
            return
        self._update(
            job_id,
            status="succeeded",
            completed_at=datetime.now(timezone.utc),
            result=_jsonable(result),
        )
        self._append_id(job_id, "Ausfuehrung abgeschlossen.")

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)

    def _append_id(self, job_id: str, message: str) -> None:
        with self._lock:
            self._append(self._jobs[job_id], message)

    @staticmethod
    def _append(job: JobRecord, message: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
        job.logs.append(f"[{stamp}] {message}")


def _copy_job(job: JobRecord) -> JobRecord:
    return JobRecord(
        id=job.id,
        name=job.name,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        result=job.result,
        error=job.error,
        logs=list(job.logs),
    )


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value
