from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from weather_ai.config import Settings
from weather_ai.ml import TrainingResult
from weather_ai.service import WeatherService
from weather_ai.stations import StationInfo, StationScope


def test_service_trains_global_models_in_global_directory():
    settings = replace(Settings.from_env(), model_dir=Path("models-root"))

    with (
        patch("weather_ai.service.WeatherService._comparison_points", return_value=[]),
        patch("weather_ai.service.train_models", return_value=TrainingResult(False, "no data")) as train_models,
    ):
        WeatherService(settings).train()

    assert train_models.call_args.args[1] == Path("models-root") / "global"


def test_service_trains_station_models_in_station_directory():
    settings = replace(Settings.from_env(), model_dir=Path("models-root"))
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
        WeatherService(settings).train(scope)

    assert train_models.call_args.args[1] == Path("models-root") / "stations" / "wetterdaten-gl-mitte"
