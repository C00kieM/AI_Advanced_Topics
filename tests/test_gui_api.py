from dataclasses import replace
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from unittest.mock import patch
import json
import socket
import urllib.request

from weather_ai.api import create_app
from weather_ai.config import Settings


def test_gui_status_endpoint_returns_safe_structured_payload():
    settings = _settings("status")
    with running_app(settings) as base_url:
        status_code, payload = request_json(base_url, "/status?live=false")

    assert status_code == 200
    assert payload["config"]["influx_token"] == "gesetzt"
    assert payload["local_cache"]["rows"] == 1
    assert payload["dwd_data"]["forecast_rows"] == 0
    assert "token" not in payload["config"].values()


def test_job_endpoint_starts_and_exposes_background_result():
    settings = _settings("archive-job")
    with patch("weather_ai.service.WeatherService.archive_dwd_forecast", return_value={"written_rows": 3}):
        with running_app(settings) as base_url:
            status_code, payload = request_json(base_url, "/jobs/archive-dwd-forecast", method="POST")
            assert status_code == 200
            finished = _wait_for_api_job(base_url, payload["id"])

    assert finished["status"] == "succeeded"
    assert finished["result"] == {"written_rows": 3}


def test_terminal_rejects_arbitrary_shell_command():
    settings = _settings("terminal-reject")

    with running_app(settings) as base_url:
        status_code, payload = request_json(
            base_url,
            "/terminal/command",
            method="POST",
            body={"command": "rm -rf /"},
            expect_error=True,
        )

    assert status_code == 400
    assert "Slash-Kommandos" in payload["detail"]


def test_terminal_known_command_can_start_job():
    settings = _settings("terminal-job")

    with patch("weather_ai.service.WeatherService.train", return_value={"trained": False}):
        with running_app(settings) as base_url:
            status_code, payload = request_json(
                base_url,
                "/terminal/command",
                method="POST",
                body={"command": "/train"},
            )
            assert status_code == 200
            assert payload["type"] == "job"
            finished = _wait_for_api_job(base_url, payload["job"]["id"])

    assert finished["status"] == "succeeded"
    assert finished["result"] == {"trained": False}


def _settings(name: str) -> Settings:
    return replace(
        Settings.from_env(),
        influx_url="http://influx.example",
        influx_token="token",
        influx_org="org",
        influx_bucket="iot",
        local_measurement="wetterdaten-gl-fw-2",
        dwd_station_id="",
        mosmix_station_id="10513",
        model_dir=Path("tests/fixtures/missing-models"),
        local_cache_path=Path("tests/fixtures/local_weather_history.csv"),
        local_cache_sync_on_startup=False,
        dwd_data_path=Path(f"tests/fixtures/missing-dwd-{name}.csv"),
    )


class running_app:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.port = _free_port()
        self.server = None
        self.thread = None

    def __enter__(self):
        import uvicorn

        app = create_app(settings=self.settings, sync_on_startup=False)
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="critical")
        self.server = uvicorn.Server(config)
        self.thread = Thread(target=self.server.run, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.port}"
        _wait_for_server(self.base_url)
        return self.base_url

    def __exit__(self, exc_type, exc, traceback):
        self.server.should_exit = True
        self.thread.join(timeout=5)


def _wait_for_api_job(base_url: str, job_id: str):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        status_code, payload = request_json(base_url, f"/jobs/{job_id}")
        assert status_code == 200
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish")


def request_json(base_url: str, path: str, method: str = "GET", body=None, expect_error: bool = False):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            payload = response.read().decode("utf-8")
            return response.status, json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        if not expect_error:
            raise
        payload = exc.read().decode("utf-8")
        return exc.code, json.loads(payload) if payload else {}


def _wait_for_server(base_url: str):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        try:
            request_json(base_url, "/jobs")
            return
        except Exception:  # noqa: BLE001
            sleep(0.03)
    raise AssertionError("server did not start")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
