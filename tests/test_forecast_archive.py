from datetime import datetime, timezone
from pathlib import Path

from weather_ai.forecast_archive import forecast_to_row, merge_forecast_rows, read_forecast_rows, retention_cutoff, row_to_forecast
from weather_ai.models import ForecastPoint


def test_forecast_archive_merge_deduplicates_forecasts():
    forecast = _forecast()

    merged = merge_forecast_rows([], [forecast_to_row(forecast), forecast_to_row(forecast)])

    assert len(merged) == 1
    assert merged[0]["kind"] == "forecast"
    assert merged[0]["field"] == "temperature"
    assert merged[0]["valid_at"] == forecast.valid_at.isoformat()


def test_forecast_archive_prunes_forecasts_older_than_retention_cutoff():
    cutoff = retention_cutoff(datetime(2026, 7, 6, 12, tzinfo=timezone.utc))
    old = forecast_to_row(_forecast(valid_at=datetime(2022, 1, 1, 9, tzinfo=timezone.utc)))
    current = forecast_to_row(_forecast(valid_at=datetime(2026, 5, 20, 9, tzinfo=timezone.utc)))

    merged = merge_forecast_rows([old], [current], cutoff=cutoff)

    assert merged == [current]


def test_forecast_row_conversion_preserves_fields():
    forecast = _forecast()
    row = forecast_to_row(forecast)
    loaded = row_to_forecast(row)

    assert loaded.station_id == "10513"
    assert loaded.value == 12.3
    assert loaded.unit == "degC"


def test_read_forecast_rows_stops_after_forecast_block():
    rows = read_forecast_rows(Path("tests/fixtures/dwd_weather_data.csv"))

    assert len(rows) == 1
    assert rows[0]["kind"] == "forecast"
    assert rows[0]["field"] == "temperature"


def test_read_forecast_rows_stops_when_file_starts_with_observations():
    path = Path("tests/fixtures/dwd_weather_observation_then_forecast.csv")

    assert read_forecast_rows(path) == []


def _forecast(valid_at: datetime | None = None) -> ForecastPoint:
    valid_at = valid_at or datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc)
    return ForecastPoint(
        source="dwd-opendata-mosmix",
        station_id="10513",
        variable="temperature",
        value=12.3,
        issued_at=datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc),
        valid_at=valid_at,
        horizon_hours=1.0,
        unit="degC",
        raw_name="TTT",
    )
