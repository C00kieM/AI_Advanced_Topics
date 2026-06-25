from dataclasses import replace
from datetime import datetime, timedelta, timezone

from weather_ai.config import Settings
from weather_ai.gui_status import _observation_days_for_forecasts
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
