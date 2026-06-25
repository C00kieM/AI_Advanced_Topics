from pathlib import Path

from weather_ai.gui import _webview_storage_path


def test_webview_storage_path_can_be_configured(monkeypatch):
    path = Path("tests/fixtures")
    monkeypatch.setenv("WEATHER_GUI_STORAGE_PATH", str(path))

    assert _webview_storage_path() == path
