from datetime import datetime, timezone
from pathlib import Path

from weather_ai.forecast_archive import forecast_to_row, merge_forecast_rows, read_forecast_rows, row_to_forecast
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


def test_read_forecast_rows_stops_after_forecast_block():
    rows = read_forecast_rows(Path("tests/fixtures/dwd_weather_data.csv"))

    assert len(rows) == 1
    assert rows[0]["kind"] == "forecast"
    assert rows[0]["field"] == "temperature"


def test_read_forecast_rows_stops_when_file_starts_with_observations(tmp_path):
    path = tmp_path / "dwd_weather_data.csv"
    path.write_text(
        "\n".join(
            [
                "kind,time,station_id,dataset,field,value,quality,source_url,source,issued_at,valid_at,horizon_hours,unit,raw_name",
                "observation,2026-05-20T09:00:00+00:00,02667,air_temperature,TT_10,10,1,http://example,dwd-cdc,,,,,",
                "forecast,2026-05-20T10:00:00+00:00,10513,forecast,temperature,12.3,,,dwd-opendata-mosmix,2026-05-20T08:00:00+00:00,2026-05-20T10:00:00+00:00,2,degC,TTT",
            ]
        ),
        encoding="utf-8",
    )

    assert read_forecast_rows(path) == []


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
