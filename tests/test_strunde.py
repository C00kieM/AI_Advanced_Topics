from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from weather_ai.chat import ChatService
from weather_ai.config import Settings
from weather_ai.daily_profile import DailyProfile
from weather_ai.models import ForecastPoint, LocalObservation
from weather_ai.strunde import _latest_forecasts_by_valid_time, build_rain_level_model
from weather_ai.strunde_cache import StrundeLevelObservation


def _fixture_settings(**overrides):
    return replace(
        Settings.from_env(),
        strunde_cache_path=Path("tests/fixtures/strunde_water_level.csv"),
        strunde_measurement="pegel-strunde",
        strunde_level_field="water_level_cm",
        **overrides,
    )


def test_strunde_current_question_uses_level_cache():
    settings = _fixture_settings()

    answer = ChatService(settings).answer("Wie hoch ist der Pegel der Strunde aktuell?")

    assert "Die Strunde liegt aktuell bei 43 cm." in answer
    assert "Letzter Pegelwert" in answer
    assert "Zusammenhang Regen/Pegel" in answer


def test_strunde_historical_question_uses_requested_day():
    settings = _fixture_settings()

    answer = ChatService(settings).answer("Wie war der Wasserstand der Strunde am 30.06.2026?")

    assert "den 30.06.2026" in answer
    assert "Pegel: im Mittel 43 cm, Spanne 41 cm bis 45 cm." in answer
    assert "Niederschlag:" in answer


def test_strunde_forecast_explains_missing_forecast_archive():
    settings = replace(
        _fixture_settings(),
        dwd_data_path=Path("tests/fixtures/missing-strunde-forecast.csv"),
    )

    with patch("weather_ai.strunde.StrundeService._current_forecasts", return_value=[]):
        answer = ChatService(settings).answer("Was passiert morgen mit dem Pegel der Strunde?")

    assert "Die Strunde liegt aktuell bei 43 cm." in answer
    assert "Niederschlag: keine gespeicherte DWD-Niederschlagsprognose" in answer
    assert "Pegelprognose" in answer


def test_strunde_future_date_is_treated_as_forecast_question():
    settings = replace(
        _fixture_settings(),
        dwd_data_path=Path("tests/fixtures/missing-strunde-forecast.csv"),
    )

    with patch("weather_ai.strunde.StrundeService._current_forecasts", return_value=[]):
        answer = ChatService(settings).answer("Wie hoch ist der Pegel der Strunde am 10.07.2026?")

    assert "Die Strunde liegt aktuell bei 43 cm." in answer
    assert "Pegelprognose" in answer
    assert "keine gespeicherte DWD-Niederschlagsprognose" in answer


def test_strunde_forecast_uses_live_dwd_when_archive_has_no_precipitation():
    settings = replace(
        _fixture_settings(),
        dwd_data_path=Path("tests/fixtures/missing-strunde-forecast.csv"),
    )
    forecast = ForecastPoint(
        source="dwd",
        station_id="10513",
        variable="precipitation",
        value=3.0,
        issued_at=datetime.now(timezone.utc),
        valid_at=datetime.now(timezone.utc) + timedelta(hours=6),
        horizon_hours=6,
    )

    with (
        patch("weather_ai.strunde.StrundeService._current_forecasts", return_value=[forecast]),
        patch("weather_ai.strunde.StrundeService.rain_level_model") as model,
    ):
        model.return_value = type(
            "Model",
            (),
            {
                "usable": False,
                "intercept_cm": 0.0,
                "slope_cm_per_mm": 0.0,
                "lag_hours": 0,
                "samples": 12,
                "correlation": 0.01,
            },
        )()
        answer = ChatService(settings).answer("Was passiert morgen mit dem Pegel der Strunde?")

    assert "Meine vorsichtige Prognose" in answer
    assert "leicht steigend" in answer
    assert "Niederschlag: DWD erwartet" in answer


def test_strunde_offline_mode_does_not_fetch_live_when_archive_is_missing():
    settings = replace(
        _fixture_settings(),
        offline_mode=True,
        dwd_data_path=Path("tests/fixtures/missing-strunde-forecast.csv"),
    )

    with (
        patch("weather_ai.strunde.MosmixClient.fetch_forecasts", side_effect=AssertionError("live fetch not expected")),
        patch("weather_ai.strunde.DwdClient.fetch_forecasts", side_effect=AssertionError("live fetch not expected")),
    ):
        answer = ChatService(settings).answer("Was passiert morgen mit dem Pegel der Strunde?")

    assert "keine gespeicherte DWD-Niederschlagsprognose" in answer


def test_strunde_offline_forecast_uses_saved_daily_precipitation_profile():
    settings = replace(
        _fixture_settings(),
        offline_mode=True,
        dwd_data_path=Path("tests/fixtures/missing-strunde-forecast.csv"),
    )
    now = datetime(2026, 7, 7, 8, tzinfo=timezone.utc)
    profile = DailyProfile(
        generated_at=now,
        source="dwd",
        target_date=now.date(),
        variable="precipitation",
        min_value=0.0,
        max_value=1.0,
        min_at=now,
        max_at=now + timedelta(hours=6),
        avg_value=0.5,
        points=4,
        issued_at=now - timedelta(hours=1),
    )

    with (
        patch("weather_ai.strunde.datetime") as mocked_datetime,
        patch("weather_ai.strunde.latest_profiles_for_date", return_value=[profile]),
        patch("weather_ai.strunde.MosmixClient.fetch_forecasts", side_effect=AssertionError("live fetch not expected")),
        patch("weather_ai.strunde.DwdClient.fetch_forecasts", side_effect=AssertionError("live fetch not expected")),
    ):
        mocked_datetime.now.return_value = now
        mocked_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        answer = ChatService(settings).answer("Was passiert morgen mit dem Pegel der Strunde?")

    assert "Meine vorsichtige Prognose" in answer
    assert "Niederschlag: DWD erwartet" in answer


def test_strunde_forecast_deduplicates_archived_runs_by_valid_time():
    valid_at = datetime(2026, 7, 8, 6, tzinfo=timezone.utc)
    older = ForecastPoint("dwd", "10513", "precipitation", 10.0, datetime(2026, 7, 7, 0, tzinfo=timezone.utc), valid_at, 30)
    newer = ForecastPoint("dwd", "10513", "precipitation", 2.0, datetime(2026, 7, 7, 6, tzinfo=timezone.utc), valid_at, 24)

    deduped = _latest_forecasts_by_valid_time([older, newer])

    assert deduped == [newer]


def test_strunde_question_routes_before_weather_station_ambiguity():
    settings = _fixture_settings()
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
        _fixture_settings(),
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
    settings = _fixture_settings()
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
