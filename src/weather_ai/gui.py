from __future__ import annotations

from threading import Thread
from time import monotonic, sleep
import os
from pathlib import Path
import secrets
import socket
from urllib.parse import quote
import urllib.request


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
WINDOW_TITLE = "Weather Ops Admin View"


def main() -> None:
    host = os.getenv("WEATHER_GUI_HOST", DEFAULT_HOST)
    port = int(os.getenv("WEATHER_GUI_PORT", str(DEFAULT_PORT)))
    if not _port_available(host, port):
        port = _find_free_port(host)

    from .api import create_app

    gui_token = secrets.token_urlsafe(24)
    app = create_app(sync_on_startup=False, gui_enabled=True, gui_token=gui_token)
    server = _start_server(app, host, port)
    url = f"http://{host}:{port}/?gui_token={quote(gui_token)}"
    _wait_for_server(url)

    try:
        import webview
    except Exception as exc:  # noqa: BLE001
        server.should_exit = True
        raise SystemExit(
            "pywebview ist nicht installiert. Installiere das Projekt erneut mit den aktuellen "
            "Abhaengigkeiten, z.B. `pip install -e .`."
        ) from exc

    try:
        webview.create_window(
            WINDOW_TITLE,
            url,
            width=1380,
            height=880,
            min_size=(980, 680),
            text_select=True,
        )
        webview.start(private_mode=False, storage_path=str(_webview_storage_path()))
    finally:
        server.should_exit = True


def _start_server(app, host: str, port: int):
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    return server


def _wait_for_server(url: str, timeout: float = 12.0) -> None:
    deadline = monotonic() + timeout
    last_error: Exception | None = None
    while monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:  # noqa: S310
                if response.status < 500:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            sleep(0.15)
    raise RuntimeError(f"GUI-Server konnte nicht gestartet werden: {last_error}")


def _find_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _webview_storage_path() -> Path:
    configured = os.getenv("WEATHER_GUI_STORAGE_PATH")
    if configured:
        path = Path(configured)
    else:
        local_app_data = os.getenv("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        path = base / "WeatherAI" / "WebView2"
    path.mkdir(parents=True, exist_ok=True)
    return path


if __name__ == "__main__":
    main()
