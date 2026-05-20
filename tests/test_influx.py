from datetime import datetime, timezone

from weather_ai.config import Settings
from weather_ai.influx import InfluxClient
from weather_ai.influx import forecast_to_line_protocol, latest_observations_flux
from weather_ai.models import ForecastPoint


def test_latest_observations_flux_targets_measurement():
    flux = latest_observations_flux("iot", "wetterdaten-gl-fw-1", 365)
    assert 'from(bucket: "iot")' in flux
    assert 'r["_measurement"] == "wetterdaten-gl-fw-1"' in flux
    assert "|> last()" in flux


def test_forecast_line_protocol_contains_tags_and_timestamp():
    point = ForecastPoint(
        source="dwd.api.bund.dev",
        station_id="G005",
        variable="temperature",
        value=12.3,
        issued_at=datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc),
        valid_at=datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc),
        horizon_hours=1.0,
        raw_name="temperature",
    )

    line = forecast_to_line_protocol(point, "dwd_forecast")

    assert line.startswith("dwd_forecast,")
    assert "station_id=G005" in line
    assert "variable=temperature" in line
    assert "issued_at=2026-05-20T08:00:00+00:00" in line
    assert "horizon_hours=1.000" in line
    assert "value=12.3" in line
    assert line.endswith("000000000")


def test_archived_forecasts_parse_value_rows():
    class FakeInflux(InfluxClient):
        def forecast_rows(self, since_days=30):  # noqa: ARG002
            return [
                {
                    "_time": "2026-05-20T09:00:00Z",
                    "_value": "12.3",
                    "station_id": "G005",
                    "variable": "temperature",
                    "issued_at": "2026-05-20T08:00:00+00:00",
                    "horizon_hours": "1.000",
                    "source": "dwd.api.bund.dev",
                    "raw_name": "temperature",
                }
            ]

    settings = Settings.from_env()
    forecasts = FakeInflux(settings).archived_forecasts()

    assert len(forecasts) == 1
    assert forecasts[0].variable == "temperature"
    assert forecasts[0].valid_at == datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc)
