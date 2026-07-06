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


def test_gui_status_endpoint_returns_dwd_time_ranges():
    settings = replace(_settings("status-dwd-range"), dwd_data_path=Path("tests/fixtures/dwd_weather_data.csv"))
    with running_app(settings) as base_url:
        status_code, payload = request_json(base_url, "/status?live=false")

    assert status_code == 200
    assert payload["dwd_data"]["observation_min_time"] == "2026-05-20T09:00:00+00:00"
    assert payload["dwd_data"]["observation_max_time"] == "2026-05-20T09:00:00+00:00"
    assert payload["dwd_data"]["min_valid_at"] == "2026-05-20T09:00:00+00:00"
    assert payload["dwd_data"]["max_valid_at"] == "2026-05-20T09:00:00+00:00"


def test_gui_status_endpoint_defaults_to_fast_non_live_status():
    settings = _settings("status-default")
    with running_app(settings) as base_url:
        status_code, payload = request_json(base_url, "/status")

    assert status_code == 200
    assert payload["live"]["checked"] is False


def test_desktop_ui_is_not_served_by_default():
    settings = _settings("api-only")
    with running_app(settings) as base_url:
        status_code, _ = request_raw(base_url, "/", expect_error=True)

    assert status_code == 404


def test_desktop_ui_requires_gui_token():
    settings = _settings("gui-token")
    with running_app(settings, gui_enabled=True, gui_token="secret") as base_url:
        status_code, _ = request_raw(base_url, "/", expect_error=True)
        assert status_code == 404

        status_code, html = request_raw(base_url, "/?gui_token=secret")
        assert status_code == 200
        assert "FHDW" in html
        assert "Weather Ops Admin View" in html
        assert "Fachhochschule der Wirtschaft" in html
        assert "/gui/static/app.js" in html
        assert "gui_token=secret" in html
        assert "chat-statusbar" not in html

        status_code, _ = request_raw(base_url, "/gui/static/app.js", expect_error=True)
        assert status_code == 404

        status_code, javascript = request_raw(base_url, "/gui/static/app.js?gui_token=secret")
        assert status_code == 200
        assert "refreshAll" in javascript

        status_code, styles = request_raw(base_url, "/gui/static/styles.css?gui_token=secret")
        assert status_code == 200
        assert "body.view-chat" in styles
        assert "background: #f6f7fb" in styles
        assert ".chat-output .line.user" in styles


def test_chat_info_lists_safe_commands():
    settings = _settings("chat-info")
    with running_app(settings) as base_url:
        status_code, payload = request_json(base_url, "/chat", method="POST", body={"question": "/info"})

    assert status_code == 200
    assert payload["type"] == "info"
    assert "/status" in payload["answer"]
    assert "/train" in payload["answer"]
    assert {item["command"] for item in payload["commands"]} >= {"/info", "/status", "/compare", "/train", "/sync-strunde"}


def test_chat_slash_command_can_start_job():
    settings = _settings("chat-job")

    with patch("weather_ai.service.WeatherService.train", return_value={"trained": False}):
        with running_app(settings) as base_url:
            status_code, payload = request_json(
                base_url,
                "/chat",
                method="POST",
                body={"question": "/train"},
            )
            assert status_code == 200
            assert payload["type"] == "job"
            finished = _wait_for_api_job(base_url, payload["job"]["id"])

    assert finished["status"] == "succeeded"
    assert finished["result"] == {"trained": False}


def test_chat_natural_job_action_requires_confirmation():
    settings = _settings("chat-confirm")

    with running_app(settings) as base_url:
        status_code, payload = request_json(
            base_url,
            "/chat",
            method="POST",
            body={"question": "Bitte trainiere die Modelle"},
        )

    assert status_code == 200
    assert payload["type"] == "confirmation"
    assert payload["command"] == "/train"
    assert "bestaetige" in payload["answer"]


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


def test_terminal_compare_does_not_query_influx_when_forecasts_are_missing():
    settings = _settings("terminal-compare-no-forecast")

    with patch(
        "weather_ai.influx.InfluxClient.local_rows_for_training",
        side_effect=AssertionError("compare must not fall back to Influx"),
    ):
        with running_app(settings) as base_url:
            status_code, payload = request_json(
                base_url,
                "/terminal/command",
                method="POST",
                body={"command": "/compare"},
            )

    assert status_code == 200
    assert payload["type"] == "comparison"
    assert payload["comparison"]["pairs"] == 0


def test_chat_compare_station_argument_uses_station_scope():
    settings = replace(_settings("chat-compare-station"), local_cache_path=Path("tests/fixtures/local_weather_multi_station.csv"))

    with patch("weather_ai.service.WeatherService.latest_comparison_summary", return_value={"pairs": 2, "summary": {}}) as compare:
        with running_app(settings) as base_url:
            status_code, payload = request_json(
                base_url,
                "/chat",
                method="POST",
                body={"question": "/compare station mitte"},
            )

    assert status_code == 200
    assert payload["type"] == "comparison"
    assert payload["station_scope"]["kind"] == "single"
    assert payload["station_scope"]["measurements"] == ["wetterdaten-gl-mitte"]
    station_scope = compare.call_args.args[0]
    assert station_scope.measurements == {"wetterdaten-gl-mitte"}


def test_chat_train_station_argument_starts_station_job():
    settings = replace(_settings("chat-train-station"), local_cache_path=Path("tests/fixtures/local_weather_multi_station.csv"))

    with patch("weather_ai.service.WeatherService.train", return_value={"trained": False}) as train:
        with running_app(settings) as base_url:
            status_code, payload = request_json(
                base_url,
                "/chat",
                method="POST",
                body={"question": "/train station mitte"},
            )
            assert status_code == 200
            assert payload["type"] == "job"
            assert payload["station_scope"]["measurements"] == ["wetterdaten-gl-mitte"]
            finished = _wait_for_api_job(base_url, payload["job"]["id"])

    assert finished["status"] == "succeeded"
    station_scope = train.call_args.args[0]
    assert station_scope.measurements == {"wetterdaten-gl-mitte"}


def test_chat_sync_strunde_starts_strunde_job():
    settings = _settings("chat-sync-strunde")

    with patch("weather_ai.api.StrundeLevelCsvCache.sync", return_value={"written_rows": 1}) as sync:
        with running_app(settings) as base_url:
            status_code, payload = request_json(
                base_url,
                "/chat",
                method="POST",
                body={"question": "/sync-strunde"},
            )
            assert status_code == 200
            assert payload["type"] == "job"
            finished = _wait_for_api_job(base_url, payload["job"]["id"])

    assert finished["status"] == "succeeded"
    sync.assert_called_once()


def test_open_data_file_endpoint_only_allows_known_app_files():
    settings = replace(
        _settings("open-file"),
        dwd_data_path=Path("tests/fixtures/dwd_weather_data.csv"),
        strunde_cache_path=Path("tests/fixtures/strunde_water_level.csv"),
    )

    with patch("weather_ai.api._open_local_file") as opener:
        with running_app(settings) as base_url:
            status_code, payload = request_json(
                base_url,
                "/files/open",
                method="POST",
                body={"target": "local-cache"},
            )
            assert status_code == 200
            assert payload["target"] == "local-cache"
            assert payload["label"] == "Lokale CSV"
            opener.assert_called_once_with(settings.local_cache_path)

            opener.reset_mock()
            status_code, payload = request_json(
                base_url,
                "/files/open",
                method="POST",
                body={"target": "strunde-cache"},
            )
            assert status_code == 200
            assert payload["target"] == "strunde-cache"
            assert payload["label"] == "Strunde-Pegel-CSV"
            opener.assert_called_once_with(settings.strunde_cache_path)

            opener.reset_mock()
            status_code, payload = request_json(
                base_url,
                "/files/open",
                method="POST",
                body={"target": "dwd-data"},
            )
            assert status_code == 200
            assert payload["target"] == "dwd-data"
            assert payload["label"] == "DWD-CSV"
            opener.assert_called_once_with(settings.dwd_data_path)

            status_code, payload = request_json(
                base_url,
                "/files/open",
                method="POST",
                body={"target": "../.env"},
                expect_error=True,
            )

    assert status_code == 400
    assert "Unbekannte Datei" in payload["detail"]


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
        strunde_cache_path=Path("tests/fixtures/strunde_water_level.csv"),
    )


class running_app:
    def __init__(self, settings: Settings, gui_enabled: bool = False, gui_token: str | None = None):
        self.settings = settings
        self.gui_enabled = gui_enabled
        self.gui_token = gui_token
        self.port = _free_port()
        self.server = None
        self.thread = None

    def __enter__(self):
        import uvicorn

        app = create_app(
            settings=self.settings,
            sync_on_startup=False,
            gui_enabled=self.gui_enabled,
            gui_token=self.gui_token,
        )
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


def request_raw(base_url: str, path: str, expect_error: bool = False):
    request = urllib.request.Request(f"{base_url}{path}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if not expect_error:
            raise
        return exc.code, exc.read().decode("utf-8")


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
