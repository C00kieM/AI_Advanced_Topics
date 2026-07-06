from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from weather_ai.chat import ChatService
from weather_ai.config import Settings
from weather_ai.models import ForecastPoint, LocalObservation
from weather_ai.strunde import build_rain_level_model
from weather_ai.strunde_cache import StrundeLevelObservation


def test_strunde_current_question_uses_level_cache():
    settings = replace(Settings.from_env(), strunde_cache_path=Path("tests/fixtures/strunde_water_level.csv"))

    answer = ChatService(settings).answer("Wie hoch ist der Pegel der Strunde aktuell?")

    assert "Die Strunde liegt aktuell bei 43 cm." in answer
    assert "Letzter Pegelwert" in answer
    assert "Zusammenhang Regen/Pegel" in answer


def test_strunde_historical_question_uses_requested_day():
    settings = replace(Settings.from_env(), strunde_cache_path=Path("tests/fixtures/strunde_water_level.csv"))

    answer = ChatService(settings).answer("Wie war der Wasserstand der Strunde am 30.06.2026?")

    assert "den 30.06.2026" in answer
    assert "Pegel: im Mittel 43 cm, Spanne 41 cm bis 45 cm." in answer
    assert "Niederschlag:" in answer


def test_strunde_forecast_explains_missing_forecast_archive():
    settings = replace(
        Settings.from_env(),
        strunde_cache_path=Path("tests/fixtures/strunde_water_level.csv"),
        dwd_data_path=Path("tests/fixtures/missing-strunde-forecast.csv"),
    )

    answer = ChatService(settings).answer("Was passiert morgen mit dem Pegel der Strunde?")

    assert "Die Strunde liegt aktuell bei 43 cm." in answer
    assert "Niederschlag: keine gespeicherte DWD-Niederschlagsprognose" in answer
    assert "Pegelprognose" in answer


def test_strunde_future_date_is_treated_as_forecast_question():
    settings = replace(
        Settings.from_env(),
        strunde_cache_path=Path("tests/fixtures/strunde_water_level.csv"),
        dwd_data_path=Path("tests/fixtures/missing-strunde-forecast.csv"),
    )

    answer = ChatService(settings).answer("Wie hoch ist der Pegel der Strunde am 10.07.2026?")

    assert "Die Strunde liegt aktuell bei 43 cm." in answer
    assert "Pegelprognose" in answer
    assert "keine gespeicherte DWD-Niederschlagsprognose" in answer


def test_strunde_question_routes_before_weather_station_ambiguity():
    settings = replace(Settings.from_env(), strunde_cache_path=Path("tests/fixtures/strunde_water_level.csv"))
    rows = [
        {"time": "2026-06-30T08:00:00+00:00", "measurement": "wetterdaten-gl-a", "field": "Lufttemperatur", "value": "20"},
        {"time": "2026-06-30T08:00:00+00:00", "measurement": "wetterdaten-a", "field": "Lufttemperatur", "value": "22"},
    ]

    with patch("weather_ai.stations.read_cache_rows", return_value=rows):
        answer = ChatService(settings).answer("Wie hoch ist der Pegel an Station A der Strunde?")

    assert "Die Strunde liegt aktuell" in answer
    assert "nicht eindeutig" not in answer


def test_strunde_rain_model_uses_configured_rain_measurements():
    settings = replace(
        Settings.from_env(),
        strunde_cache_path=Path("tests/fixtures/strunde_water_level.csv"),
        strunde_rain_measurements=("rain-near-strunde",),
    )

    with patch("weather_ai.strunde.WeatherStationCsvCache.observations_between", return_value=[]) as observations_between:
        ChatService(settings).answer("Wie hoch ist der Pegel der Strunde aktuell?")

    assert observations_between.call_args.kwargs["measurements"] == {"rain-near-strunde"}


def test_rain_level_model_finds_positive_rain_level_correlation():
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rain_values = [0, 1, 2, 4, 7, 3, 6, 9, 5]
    level_values = [40.0]
    for value in rain_values:
        level_values.append(level_values[-1] + value * 1.5)
    levels = [
        StrundeLevelObservation("pegel-strunde", "water_level_cm", value, base + timedelta(hours=6 * index))
        for index, value in enumerate(level_values)
    ]
    rain = [
        LocalObservation("station", "Niederschlag", value, base + timedelta(hours=6 * index - 3))
        for index, value in enumerate(rain_values)
    ]

    model = build_rain_level_model(levels, rain)

    assert model.samples >= 6
    assert model.correlation > 0.2
    assert model.slope_cm_per_mm > 0


def test_strunde_forecast_uses_rain_model_when_available():
    settings = replace(Settings.from_env(), strunde_cache_path=Path("tests/fixtures/strunde_water_level.csv"))
    forecast = ForecastPoint(
        source="dwd",
        station_id="10513",
        variable="precipitation",
        value=4.0,
        issued_at=datetime.now(timezone.utc),
        valid_at=datetime.now(timezone.utc) + timedelta(hours=3),
        horizon_hours=3,
    )

    with (
        patch("weather_ai.strunde.StrundeService.rain_level_model") as model,
        patch("weather_ai.strunde.ForecastCsvArchive.read", return_value=[forecast]),
    ):
        model.return_value = type(
            "Model",
            (),
            {
                "usable": True,
                "intercept_cm": 0.0,
                "slope_cm_per_mm": 1.5,
                "lag_hours": 3,
                "samples": 12,
                "correlation": 0.7,
            },
        )()
        answer = ChatService(settings).answer("Wie wird der Pegel der Strunde morgen?")

    assert "schaetze ich den Pegel" in answer
    assert "49 cm" in answer
    assert "Niederschlag: DWD erwartet" in answer
