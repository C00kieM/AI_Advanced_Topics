from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch

from weather_ai.config import Settings
from weather_ai.ml import TrainingResult
from weather_ai.service import WeatherService
from weather_ai.stations import StationInfo, StationScope


def test_service_trains_global_models_in_global_directory():
    settings = replace(Settings.from_env(), model_dir=Path(".test-runtime") / "models-global")

    with (
        patch("weather_ai.service.WeatherService._comparison_points", return_value=[]),
        patch("weather_ai.service.train_models", return_value=TrainingResult(False, "no data")) as train_models,
    ):
        result = WeatherService(settings).train()

    assert train_models.call_args.args[1] == settings.model_dir / "global"
    metrics_path = settings.model_dir / "global" / "training_metrics.json"
    assert result["metrics_saved_to"] == str(metrics_path)
    assert json.loads(metrics_path.read_text(encoding="utf-8"))["trained"] is False


def test_service_trains_station_models_in_station_directory():
    settings = replace(Settings.from_env(), model_dir=Path(".test-runtime") / "models-station")
    scope = StationScope(
        kind="single",
        measurements={"wetterdaten-gl-mitte"},
        label="Mitte",
        station=StationInfo("wetterdaten-gl-mitte", "Mitte", ("mitte",)),
    )

    with (
        patch("weather_ai.service.WeatherService._comparison_points", return_value=[]),
        patch("weather_ai.service.train_models", return_value=TrainingResult(False, "no data")) as train_models,
    ):
        result = WeatherService(settings).train(scope)

    expected_dir = settings.model_dir / "stations" / "wetterdaten-gl-mitte"
    assert train_models.call_args.args[1] == expected_dir
    assert result["metrics_saved_to"] == str(expected_dir / "training_metrics.json")


def test_service_saves_comparison_summary_in_scoped_model_directory():
    settings = replace(Settings.from_env(), model_dir=Path(".test-runtime") / "models-compare")

    with patch("weather_ai.service.WeatherService._comparison_points", return_value=[]):
        result = WeatherService(settings).latest_comparison_summary()

    summary_path = settings.model_dir / "global" / "comparison_summary.json"
    assert result["saved_to"] == str(summary_path)
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["pairs"] == 0
    assert payload["scope"]["kind"] == "all"
