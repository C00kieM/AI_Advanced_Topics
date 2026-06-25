from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from weather_ai.config import Settings
from weather_ai.diagnostics import build_status
from weather_ai.influx import InfluxError


def test_build_status_uses_local_cache_when_influx_is_unreachable():
    settings = replace(
        Settings.from_env(),
        local_measurement="wetterdaten-gl-fw-2",
        local_cache_path=Path("tests/fixtures/local_weather_history.csv"),
        local_cache_retention_days=3650,
        dwd_station_id="",
        mosmix_station_id="",
    )

    with patch(
        "weather_ai.diagnostics.InfluxClient.latest_observations",
        side_effect=InfluxError("InfluxDB query failed: timeout"),
    ):
        report = build_status(settings)

    assert report.influx_ok is False
    assert report.local_latest
    assert report.local_latest[0].measurement == "wetterdaten-gl-fw-2"
    assert any("CSV-Cachewerte" in warning for warning in report.warnings)
