from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import mock_open, patch

from weather_ai.daily_profile import append_profiles, latest_profiles_for_date, profile_to_row, profiles_for_forecasts
from weather_ai.models import ForecastPoint


def test_profiles_for_forecasts_calculates_daily_temperature_range():
    issued_at = datetime(2026, 6, 25, 6, tzinfo=timezone.utc)
    forecasts = [
        _forecast(12, 18.0, issued_at),
        _forecast(15, 25.0, issued_at),
        _forecast(18, 21.0, issued_at),
    ]

    profiles = profiles_for_forecasts(forecasts, source="dwd", generated_at=issued_at)

    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.min_value == 18.0
    assert profile.max_value == 25.0
    assert profile.max_at.hour == 15
    assert profile.points == 3


def test_append_profiles_persists_latest_profile_for_date():
    issued_at = datetime(2026, 6, 25, 6, tzinfo=timezone.utc)
    profiles = profiles_for_forecasts([_forecast(12, 18.0, issued_at)], source="dwd", generated_at=issued_at)

    with (
        patch("weather_ai.daily_profile.read_profile_rows", return_value=[]),
        patch("pathlib.Path.open", mock_open()),
    ):
        assert append_profiles(Path("unused.csv"), profiles) == 1


def test_latest_profiles_for_date_selects_newest_profile():
    older = profiles_for_forecasts(
        [_forecast(12, 18.0, datetime(2026, 6, 25, 6, tzinfo=timezone.utc))],
        source="dwd",
        generated_at=datetime(2026, 6, 25, 6, tzinfo=timezone.utc),
    )[0]
    newer = profiles_for_forecasts(
        [_forecast(12, 20.0, datetime(2026, 6, 25, 9, tzinfo=timezone.utc))],
        source="dwd",
        generated_at=datetime(2026, 6, 25, 9, tzinfo=timezone.utc),
    )[0]

    with patch("weather_ai.daily_profile.read_profile_rows", return_value=[profile_to_row(older), profile_to_row(newer)]):
        loaded = latest_profiles_for_date(Path("unused.csv"), newer.target_date)

    assert len(loaded) == 1
    assert loaded[0].min_value == 20.0


def _forecast(hour: int, value: float, issued_at: datetime) -> ForecastPoint:
    valid_at = datetime(2026, 6, 26, hour, tzinfo=timezone.utc)
    return ForecastPoint(
        source="dwd-opendata-mosmix",
        station_id="10513",
        variable="temperature",
        value=value,
        issued_at=issued_at,
        valid_at=valid_at,
        horizon_hours=(valid_at - issued_at).total_seconds() / 3600,
        unit="degC",
        raw_name="TTT",
    )
