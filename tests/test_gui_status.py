from dataclasses import replace
from datetime import datetime, timedelta, timezone

from weather_ai.config import Settings
from weather_ai.gui_status import _observation_days_for_forecasts, summarize_local_cache
from weather_ai.models import ForecastPoint


def test_gui_comparison_lookback_covers_oldest_forecast():
    settings = replace(Settings.from_env(), local_cache_retention_days=1095)
    forecast = ForecastPoint(
        source="dwd-opendata-mosmix",
        station_id="10513",
        variable="temperature",
        value=20.0,
        issued_at=datetime.now(timezone.utc) - timedelta(days=42, hours=1),
        valid_at=datetime.now(timezone.utc) - timedelta(days=42),
        horizon_hours=1.0,
    )

    assert _observation_days_for_forecasts(settings, [forecast]) >= 43


def test_local_cache_status_uses_selected_measurement_freshness(tmp_path):
    path = tmp_path / "local_weather_history.csv"
    path.write_text(
        "\n".join(
            [
                "time,measurement,field,value",
                f"{datetime.now(timezone.utc).isoformat()},fresh-other,Lufttemperatur,12.0",
                "2024-01-01T00:00:00+00:00,target,Lufttemperatur,8.0",
            ]
        ),
        encoding="utf-8",
    )

    summary = summarize_local_cache(path, selected_measurement="target")

    assert summary["selected_rows"] == 1
    assert summary["stale"] is True
