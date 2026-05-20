from pathlib import Path

from weather_ai.ml import train_models


def test_train_models_reports_insufficient_data():
    result = train_models([], Path(".tmp-test-models"))

    assert result.trained is False
    assert "Nicht genug" in result.message
