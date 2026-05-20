from datetime import datetime, timedelta, timezone

from weather_ai.comparison import match_forecasts_to_observations, summarize_comparisons
from weather_ai.models import ForecastPoint, LocalObservation


def test_match_forecasts_to_observations_uses_nearest_local_value():
    valid_at = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    forecast = ForecastPoint(
        source="dwd",
        station_id="G005",
        variable="temperature",
        value=20.0,
        issued_at=valid_at - timedelta(hours=3),
        valid_at=valid_at,
        horizon_hours=3,
    )
    observations = [
        LocalObservation("station", "Lufttemperatur", 19.0, valid_at + timedelta(minutes=20)),
        LocalObservation("station", "Lufttemperatur", 18.0, valid_at + timedelta(hours=2)),
    ]

    comparisons = match_forecasts_to_observations([forecast], observations)

    assert len(comparisons) == 1
    assert comparisons[0].actual_value == 19.0
    assert comparisons[0].error == -1.0
    assert summarize_comparisons(comparisons)["temperature"]["mae"] == 1.0
