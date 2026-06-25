from time import monotonic, sleep

from weather_ai.jobs import JobManager


def wait_for_status(manager: JobManager, job_id: str, status: str):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        job = manager.get(job_id)
        if job.status == status:
            return job
        sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach {status}")


def test_job_manager_records_successful_background_job():
    manager = JobManager()

    job = manager.start("Testjob", lambda: {"ok": True})
    finished = wait_for_status(manager, job.id, "succeeded")

    assert finished.result == {"ok": True}
    assert finished.error is None
    assert finished.to_payload()["duration_seconds"] >= 0
    assert any("abgeschlossen" in line for line in finished.logs)
    assert any(" in " in line for line in finished.logs)


def test_job_manager_records_failed_background_job():
    manager = JobManager()

    def fail():
        raise RuntimeError("kaputt")

    job = manager.start("Fehlerjob", fail)
    finished = wait_for_status(manager, job.id, "failed")

    assert finished.error == "kaputt"
    assert finished.to_payload()["duration_seconds"] >= 0
    assert any("Fehler" in line for line in finished.logs)
    assert any("Dauer" in line for line in finished.logs)
