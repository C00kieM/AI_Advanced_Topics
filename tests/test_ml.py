from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from weather_ai.ml import ForecastCorrectionModel, train_models
from weather_ai.models import ComparisonPoint


def test_train_models_reports_insufficient_data():
    result = train_models([], Path(".tmp-test-models"))

    assert result.trained is False
    assert "Nicht genug" in result.message


def test_train_models_calculates_rmse_with_current_sklearn_api():
    base = datetime(2026, 5, 20, tzinfo=timezone.utc)
    comparisons = [
        ComparisonPoint(
            variable="temperature",
            forecast_value=float(index),
            actual_value=float(index) + 1.0,
            error=1.0,
            forecast_valid_at=base + timedelta(hours=index),
            observation_time=base + timedelta(hours=index),
            horizon_hours=float(index % 24),
        )
        for index in range(48)
    ]

    with patch("joblib.dump", return_value=None):
        result = train_models(comparisons, Path("."), min_points=24)

    assert result.trained is True
    assert result.metrics["temperature"]["baseline_rmse"] >= 0
    assert result.metrics["temperature"]["model_rmse"] >= 0
    assert result.metrics["temperature"]["validation"] == "random_holdout"


def test_train_models_uses_holdout_for_small_training_sets():
    base = datetime(2026, 5, 20, tzinfo=timezone.utc)
    comparisons = [
        ComparisonPoint(
            variable="temperature",
            forecast_value=float(index),
            actual_value=float(index) + 2.0,
            error=2.0,
            forecast_valid_at=base + timedelta(hours=index),
            observation_time=base + timedelta(hours=index),
            horizon_hours=float(index % 12),
        )
        for index in range(24)
    ]

    with patch("joblib.dump") as dump:
        result = train_models(comparisons, Path("."), min_points=24)

    assert result.trained is True
    assert result.metrics["temperature"]["validation"] == "chronological_holdout"
    assert result.metrics["temperature"]["train_points"] == 18
    assert result.metrics["temperature"]["test_points"] == 6
    assert isinstance(dump.call_args.args[0], ForecastCorrectionModel)
