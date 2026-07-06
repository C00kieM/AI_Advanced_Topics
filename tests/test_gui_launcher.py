from pathlib import Path

from weather_ai.gui import WINDOW_TITLE, _webview_storage_path


def test_desktop_window_uses_desktop_app_branding():
    assert WINDOW_TITLE == "Weather Ops Admin View"


def test_webview_storage_path_can_be_configured(monkeypatch):
    path = Path("tests/fixtures")
    monkeypatch.setenv("WEATHER_GUI_STORAGE_PATH", str(path))

    assert _webview_storage_path() == path
