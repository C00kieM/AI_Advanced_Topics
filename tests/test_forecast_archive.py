from datetime import datetime, timezone

from pathlib import Path
from uuid import uuid4

from weather_ai.config import Settings
from weather_ai.forecast_archive import ForecastCsvArchive, forecast_to_row, row_to_forecast
from weather_ai.models import ForecastPoint


def test_forecast_archive_round_trip(monkeypatch):
    path = Path(f".tmp-test-forecast-archive-{uuid4().hex}.csv")
    monkeypatch.setenv("FORECAST_ARCHIVE_PATH", str(path))
    archive = ForecastCsvArchive(Settings.from_env())
    forecast = _forecast()

    result = archive.append([forecast, forecast])
    loaded = archive.read()

    assert result.fetched == 2
    assert result.written_rows == 1
    assert len(loaded) == 1
    assert loaded[0].variable == "temperature"
    assert loaded[0].valid_at == forecast.valid_at


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
