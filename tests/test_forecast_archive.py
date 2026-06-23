from datetime import datetime, timezone

from weather_ai.forecast_archive import forecast_to_row, merge_forecast_rows, row_to_forecast
from weather_ai.models import ForecastPoint


def test_forecast_archive_merge_deduplicates_forecasts():
    forecast = _forecast()

    merged = merge_forecast_rows([], [forecast_to_row(forecast), forecast_to_row(forecast)])

    assert len(merged) == 1
    assert merged[0]["kind"] == "forecast"
    assert merged[0]["field"] == "temperature"
    assert merged[0]["valid_at"] == forecast.valid_at.isoformat()


def test_forecast_row_conversion_preserves_fields():
    forecast = _forecast()
    row = forecast_to_row(forecast)
    loaded = row_to_forecast(row)

    assert loaded.station_id == "10513"
    assert loaded.value == 12.3
    assert loaded.unit == "degC"


def _forecast() -> ForecastPoint:
    return ForecastPoint(
        source="dwd-opendata-mosmix",
        station_id="10513",
        variable="temperature",
        value=12.3,
        issued_at=datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc),
        valid_at=datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc),
        horizon_hours=1.0,
        unit="degC",
        raw_name="TTT",
    )
