from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from weather_ai.chat import ChatService, DataRange
from weather_ai.config import Settings
from weather_ai.daily_profile import DailyProfile
from weather_ai.models import ForecastPoint, LocalObservation, StatusReport


def test_chat_answer_is_rule_based_without_llm_provider():
    settings = Settings(
        influx_url="http://influx.example",
        influx_token="token",
        influx_org="org",
        influx_bucket="iot",
        local_measurement="wetterdaten-gl-fw-1",
        local_lat=None,
        local_lon=None,
        dwd_station_id="",
        dwd_base_url="https://dwd.example",
        mosmix_station_id="",
        mosmix_product="MOSMIX_L",
        mosmix_base_url="http://opendata.dwd.de",
        model_dir=Path(".tmp-test-models"),
        local_cache_path=Path("data/local_weather_history.csv"),
        local_cache_retention_days=1095,
        local_cache_sync_on_startup=False,
        dwd_cdc_base_url="http://opendata.dwd.de/climate_environment/CDC",
        dwd_historical_station_ids=["02667"],
        dwd_historical_resolution="10_minutes",
        dwd_historical_parameters=["air_temperature", "precipitation", "wind"],
        dwd_historical_retention_days=1095,
        dwd_data_path=Path("data/dwd_weather_data.csv"),
    )
    report = StatusReport(
        influx_ok=True,
        dwd_ok=False,
        local_latest=[
            LocalObservation(
                measurement="wetterdaten-gl-fw-1",
                field="Lufttemperatur",
                value=13.3,
                time=datetime(2026, 4, 16, 0, 25, tzinfo=timezone.utc),
            )
        ],
        warnings=["DWD_STATION_ID fehlt; DWD-Livecheck uebersprungen."],
    )

    with patch("weather_ai.chat.build_status", return_value=report):
        answer = ChatService(settings).answer("Wie ist die aktuelle Datenlage?")

    assert "Der neueste lokale Messwert" in answer
    assert "**13,3 °C**" in answer
    assert "Wind:" in answer
    assert "Niederschlag:" in answer
    assert "Lokaler Wind:" in answer
    assert "Lokaler Niederschlag:" in answer
    assert "DWD_STATION_ID" in answer
    assert "LLM" not in answer


def test_chat_answers_month_history_from_dwd_history_without_live_forecast():
    settings = Settings(
        influx_url="http://influx.example",
        influx_token="token",
        influx_org="org",
        influx_bucket="iot",
        local_measurement="wetterdaten-gl-fw-2",
        local_lat=None,
        local_lon=None,
        dwd_station_id="",
        dwd_base_url="https://dwd.example",
        mosmix_station_id="10513",
        mosmix_product="MOSMIX_L",
        mosmix_base_url="http://opendata.dwd.de",
        model_dir=Path(".tmp-test-models"),
        local_cache_path=Path("data/local_weather_history.csv"),
        local_cache_retention_days=1095,
        local_cache_sync_on_startup=False,
        dwd_cdc_base_url="http://opendata.dwd.de/climate_environment/CDC",
        dwd_historical_station_ids=["02667"],
        dwd_historical_resolution="10_minutes",
        dwd_historical_parameters=["air_temperature", "precipitation", "wind"],
        dwd_historical_retention_days=1095,
        dwd_data_path=Path("data/dwd_weather_data.csv"),
    )
    rows = [
        {"field": "TT_10", "value": "2.0"},
        {"field": "TT_10", "value": "4.0"},
        {"field": "RWS_10", "value": "1.5"},
        {"field": "FF_10", "value": "3.0"},
    ]

    with (
        patch(
            "weather_ai.chat._available_data_range",
            return_value=DataRange(
                start=datetime(2024, 3, 24, tzinfo=timezone.utc),
                end=datetime(2026, 6, 23, tzinfo=timezone.utc),
            ),
        ),
        patch("weather_ai.chat.WeatherStationCsvCache.observations_between", return_value=[]),
        patch(
            "weather_ai.chat._summarize_dwd_history_between",
            return_value={
                "points": 4,
                "temperature": {"count": 2, "avg": 3.0, "min": 2.0, "max": 4.0},
                "precipitation": {"count": 1, "sum": 1.5, "max": 1.5},
                "wind_speed": {"count": 1, "avg": 3.0, "min": 3.0, "max": 3.0},
            },
        ),
        patch("weather_ai.chat.MosmixClient.fetch_forecasts", side_effect=AssertionError("live forecast not expected")),
    ):
        answer = ChatService(settings).answer("wie war das wetter im M\u00e4rz 2026")

    assert "Maerz 2026" in answer
    assert "DWD-Historie" in answer
    assert "Lokale Daten (alle Stationen): Fuer diesen Zeitraum fehlen verwertbare Daten." in answer
    assert "Hinweis: Fuer eine der beiden Quellen fehlen" in answer
    assert "Historische Auswertung:" not in answer
    assert "DWD-Prognose:" not in answer
    assert "Temperatur: im Mittel **3 °C**" in answer
    assert "Wind: im Mittel 3 m/s" in answer
    assert "Niederschlag: Summe im Zeitraum: 1,5 mm" in answer


def test_chat_answers_tomorrow_with_dwd_and_local_daily_profiles():
    settings = _settings()
    target = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    profiles = [
        DailyProfile(
            generated_at=datetime(2026, 6, 25, 6, tzinfo=timezone.utc),
            source="dwd",
            target_date=target,
            variable="temperature",
            min_value=18.0,
            max_value=27.0,
            min_at=datetime(2026, 6, 26, 6, tzinfo=timezone.utc),
            max_at=datetime(2026, 6, 26, 15, tzinfo=timezone.utc),
            avg_value=22.0,
            points=24,
            issued_at=datetime(2026, 6, 25, 6, tzinfo=timezone.utc),
        ),
        DailyProfile(
            generated_at=datetime(2026, 6, 25, 6, tzinfo=timezone.utc),
            source="local-corrected",
            target_date=target,
            variable="temperature",
            min_value=17.0,
            max_value=25.0,
            min_at=datetime(2026, 6, 26, 6, tzinfo=timezone.utc),
            max_at=datetime(2026, 6, 26, 14, tzinfo=timezone.utc),
            avg_value=21.0,
            points=24,
            issued_at=datetime(2026, 6, 25, 6, tzinfo=timezone.utc),
        ),
        DailyProfile(
            generated_at=datetime(2026, 6, 25, 6, tzinfo=timezone.utc),
            source="dwd",
            target_date=target,
            variable="wind_speed",
            min_value=1.0,
            max_value=3.0,
            min_at=datetime(2026, 6, 26, 6, tzinfo=timezone.utc),
            max_at=datetime(2026, 6, 26, 15, tzinfo=timezone.utc),
            avg_value=2.0,
            points=24,
            issued_at=datetime(2026, 6, 25, 6, tzinfo=timezone.utc),
        ),
        DailyProfile(
            generated_at=datetime(2026, 6, 25, 6, tzinfo=timezone.utc),
            source="local-corrected",
            target_date=target,
            variable="wind_speed",
            min_value=0.5,
            max_value=2.0,
            min_at=datetime(2026, 6, 26, 6, tzinfo=timezone.utc),
            max_at=datetime(2026, 6, 26, 14, tzinfo=timezone.utc),
            avg_value=1.0,
            points=24,
            issued_at=datetime(2026, 6, 25, 6, tzinfo=timezone.utc),
        ),
        DailyProfile(
            generated_at=datetime(2026, 6, 25, 6, tzinfo=timezone.utc),
            source="dwd",
            target_date=target,
            variable="precipitation",
            min_value=0.0,
            max_value=0.4,
            min_at=datetime(2026, 6, 26, 6, tzinfo=timezone.utc),
            max_at=datetime(2026, 6, 26, 15, tzinfo=timezone.utc),
            avg_value=0.1,
            points=24,
            issued_at=datetime(2026, 6, 25, 6, tzinfo=timezone.utc),
        ),
        DailyProfile(
            generated_at=datetime(2026, 6, 25, 6, tzinfo=timezone.utc),
            source="local-corrected",
            target_date=target,
            variable="precipitation",
            min_value=0.0,
            max_value=0.2,
            min_at=datetime(2026, 6, 26, 6, tzinfo=timezone.utc),
            max_at=datetime(2026, 6, 26, 14, tzinfo=timezone.utc),
            avg_value=0.05,
            points=24,
            issued_at=datetime(2026, 6, 25, 6, tzinfo=timezone.utc),
        ),
    ]

    with (
        patch("weather_ai.chat.latest_profiles_for_date", return_value=profiles),
        patch("weather_ai.chat.MosmixClient.fetch_forecasts", side_effect=AssertionError("live forecast not expected")),
    ):
        answer = ChatService(settings).answer("Wie wird das Wetter morgen, wird es hei\u00df?")

    assert "Fuer morgen sieht DWD den waermsten Zeitpunkt" in answer
    assert "Temperatur: DWD **18 bis 27 °C**" in answer
    assert "lokal korrigiert **17 bis 25 °C**" in answer
    assert "Wind: DWD 1 bis 3 m/s; lokal korrigiert 0,5 bis 2 m/s." in answer
    assert "Niederschlag: DWD 0 bis 0,4 mm je Forecastpunkt; lokal korrigiert 0 bis 0,2 mm je Forecastpunkt." in answer
    assert "Tagesverlauf morgen:" not in answer
    assert "zuletzt gespeicherte Tagesprofil" not in answer


def test_chat_answers_future_month_as_planning_not_full_month_forecast():
    settings = _settings()
    now = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)
    report = StatusReport(
        generated_at=now,
        local_latest=[
            LocalObservation(
                measurement="wetterdaten-gl-fw-1",
                field="Lufttemperatur",
                value=22.1,
                time=datetime(2026, 6, 30, 8, tzinfo=timezone.utc),
            ),
            LocalObservation(
                measurement="wetterdaten-gl-fw-1",
                field="wind_speed",
                value=2.2,
                time=datetime(2026, 6, 30, 8, tzinfo=timezone.utc),
            ),
            LocalObservation(
                measurement="wetterdaten-gl-fw-1",
                field="Niederschlag",
                value=0.0,
                time=datetime(2026, 6, 30, 8, tzinfo=timezone.utc),
            ),
        ],
    )
    observations = [
        LocalObservation("wetterdaten-gl-fw-1", "Lufttemperatur", 22.0, datetime(2024, 8, 1, tzinfo=timezone.utc)),
        LocalObservation("wetterdaten-gl-fw-1", "Lufttemperatur", 26.0, datetime(2025, 8, 1, tzinfo=timezone.utc)),
        LocalObservation("wetterdaten-gl-fw-1", "wind_speed", 2.0, datetime(2024, 8, 1, tzinfo=timezone.utc)),
        LocalObservation("wetterdaten-gl-fw-1", "wind_speed", 4.0, datetime(2025, 8, 1, tzinfo=timezone.utc)),
        LocalObservation("wetterdaten-gl-fw-1", "Niederschlag", 0.0, datetime(2024, 8, 1, tzinfo=timezone.utc)),
        LocalObservation("wetterdaten-gl-fw-1", "Niederschlag", 1.5, datetime(2025, 8, 1, tzinfo=timezone.utc)),
    ]
    forecasts = [
        ForecastPoint("dwd", "10513", "temperature", 23.4, now, now + timedelta(hours=3), 3),
        ForecastPoint("dwd", "10513", "temperature", 25.1, now, now + timedelta(hours=6), 6),
        ForecastPoint("dwd", "10513", "wind_speed", 5.1, now, now + timedelta(hours=3), 3),
        ForecastPoint("dwd", "10513", "precipitation", 0.0, now, now + timedelta(hours=3), 3),
    ]

    with (
        patch("weather_ai.chat._now_utc", return_value=now),
        patch(
            "weather_ai.chat._available_data_range",
            return_value=DataRange(
                start=datetime(2024, 3, 24, tzinfo=timezone.utc),
                end=datetime(2026, 6, 30, tzinfo=timezone.utc),
            ),
        ),
        patch("weather_ai.chat.build_status", return_value=report),
        patch("weather_ai.chat.WeatherStationCsvCache.observations_between", return_value=observations),
        patch("weather_ai.chat.MosmixClient.fetch_forecasts", return_value=forecasts),
    ):
        answer = ChatService(settings).answer("Wie wird durchschnittlich das Wetter im August dieses Jahr?")

    assert "Eine verlaessliche Vorhersage fuer den gesamten August 2026" in answer
    assert "Temperatur: im Mittel **24 °C**, Spanne **22 bis 26 °C**." in answer
    assert "Wind: im Mittel 3 m/s, maximal 4 m/s." in answer
    assert "Niederschlag: Summe in den vorhandenen Monatsdaten: 1,5 mm." in answer
    assert "Temperatur: DWD erwartet etwa **23 bis 25 °C**." in answer
    assert "Wind: DWD erwartet bis zu 5,1 m/s." in answer
    assert "Niederschlag: kein Niederschlag erwartet." in answer
    assert "Forecast-vs-Ist" not in answer
    assert "Dauer" not in answer


def test_chat_rejects_weather_question_before_oldest_available_data():
    settings = _settings()
    now = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)

    with (
        patch("weather_ai.chat._now_utc", return_value=now),
        patch(
            "weather_ai.chat._available_data_range",
            return_value=DataRange(
                start=datetime(2024, 3, 24, tzinfo=timezone.utc),
                end=datetime(2026, 6, 23, tzinfo=timezone.utc),
            ),
        ),
    ):
        answer = ChatService(settings).answer("Wie war das Wetter vor 20 Jahren?")

    assert "Fuer das Jahr 2006 kann ich keine belastbare Auswertung liefern." in answer
    assert "maximal drei Jahre zurueck" in answer
    assert "Temperatur: nicht verfuegbar." in answer
    assert "Wind: nicht verfuegbar." in answer
    assert "Niederschlag: nicht verfuegbar." in answer


def test_chat_rejects_year_question_before_oldest_available_data():
    settings = _settings()
    now = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)

    with (
        patch("weather_ai.chat._now_utc", return_value=now),
        patch(
            "weather_ai.chat._available_data_range",
            return_value=DataRange(
                start=datetime(2024, 3, 24, tzinfo=timezone.utc),
                end=datetime(2026, 6, 23, tzinfo=timezone.utc),
            ),
        ),
        patch("weather_ai.chat.MosmixClient.fetch_forecasts", side_effect=AssertionError("live forecast not expected")),
    ):
        answer = ChatService(settings).answer("wie war das wetter 2020")

    assert "Fuer das Jahr 2020 kann ich keine belastbare Auswertung liefern." in answer
    assert "maximal drei Jahre zurueck" in answer
    assert "Temperatur: nicht verfuegbar." in answer
    assert "Wind: nicht verfuegbar." in answer
    assert "Niederschlag: nicht verfuegbar." in answer
    assert "naechsten verfuegbaren Prognosezeiten" not in answer


def test_chat_answers_day_relative_history_instead_of_current_forecast():
    settings = _settings()
    now = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)
    observations = [
        LocalObservation("wetterdaten-gl-fw-1", "Lufttemperatur", 19.0, datetime(2026, 6, 29, 8, tzinfo=timezone.utc)),
        LocalObservation("wetterdaten-gl-fw-1", "Lufttemperatur", 24.0, datetime(2026, 6, 29, 14, tzinfo=timezone.utc)),
        LocalObservation("wetterdaten-gl-fw-1", "wind_speed", 2.0, datetime(2026, 6, 29, 8, tzinfo=timezone.utc)),
        LocalObservation("wetterdaten-gl-fw-1", "wind_speed", 4.0, datetime(2026, 6, 29, 14, tzinfo=timezone.utc)),
        LocalObservation("wetterdaten-gl-fw-1", "Niederschlag", 0.3, datetime(2026, 6, 29, 9, tzinfo=timezone.utc)),
    ]

    with (
        patch("weather_ai.chat._now_utc", return_value=now),
        patch(
            "weather_ai.chat._available_data_range",
            return_value=DataRange(
                start=datetime(2024, 3, 24, tzinfo=timezone.utc),
                end=datetime(2026, 7, 1, tzinfo=timezone.utc),
            ),
        ),
        patch("weather_ai.chat.WeatherStationCsvCache.observations_between", return_value=observations),
        patch("weather_ai.chat._summarize_dwd_history_between", return_value={"points": 0}),
        patch("weather_ai.chat.MosmixClient.fetch_forecasts", side_effect=AssertionError("live forecast not expected")),
    ):
        answer = ChatService(settings).answer("wie war das wetter vorgestern")

    assert "vorgestern" in answer
    assert "29.06.2026" in answer
    assert "Hinweis: Fuer eine der beiden Quellen fehlen" in answer
    assert "Temperatur: im Mittel **21,5" in answer
    assert "Wind: im Mittel 3 m/s" in answer
    assert "Niederschlag: Summe im Zeitraum: 0,3 mm" in answer
    assert "DWD-Historie: Fuer diesen Zeitraum fehlen verwertbare Daten." in answer
    assert "naechsten verfuegbaren Prognosezeiten" not in answer


def test_chat_answers_combined_relative_history_periods():
    settings = _settings()
    now = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)
    observations = [
        LocalObservation("wetterdaten-gl-fw-1", "Lufttemperatur", 20.0, datetime(2026, 6, 30, 8, tzinfo=timezone.utc)),
        LocalObservation("wetterdaten-gl-fw-1", "wind_speed", 2.0, datetime(2026, 6, 30, 8, tzinfo=timezone.utc)),
        LocalObservation("wetterdaten-gl-fw-1", "Niederschlag", 0.0, datetime(2026, 6, 30, 8, tzinfo=timezone.utc)),
        LocalObservation("wetterdaten-gl-fw-1", "Lufttemperatur", 18.0, datetime(2026, 6, 24, 8, tzinfo=timezone.utc)),
        LocalObservation("wetterdaten-gl-fw-1", "wind_speed", 3.0, datetime(2026, 6, 24, 8, tzinfo=timezone.utc)),
        LocalObservation("wetterdaten-gl-fw-1", "Niederschlag", 1.2, datetime(2026, 6, 24, 8, tzinfo=timezone.utc)),
    ]

    def observations_between(start, end, fields, measurements):
        return [item for item in observations if start <= item.time < end]

    with (
        patch("weather_ai.chat._now_utc", return_value=now),
        patch(
            "weather_ai.chat._available_data_range",
            return_value=DataRange(
                start=datetime(2024, 3, 24, tzinfo=timezone.utc),
                end=datetime(2026, 7, 1, tzinfo=timezone.utc),
            ),
        ),
        patch("weather_ai.chat.WeatherStationCsvCache.observations_between", side_effect=observations_between),
        patch("weather_ai.chat._summarize_dwd_history_between", return_value={"points": 0}),
        patch("weather_ai.chat.MosmixClient.fetch_forecasts", side_effect=AssertionError("live forecast not expected")),
    ):
        answer = ChatService(settings).answer("wie war das wetter gestern und vor einer woche")

    assert "gestern" in answer
    assert "30.06.2026" in answer
    assert "vor einer Woche" in answer
    assert "24.06.2026" in answer
    assert answer.count("Lokale Daten (alle Stationen):") == 2
    assert answer.count("DWD-Historie: Fuer diesen Zeitraum fehlen verwertbare Daten.") == 2
    assert "Hinweis: Fuer eine der beiden Quellen fehlen" in answer
    assert "naechsten verfuegbaren Prognosezeiten" not in answer


def test_chat_keeps_local_history_answer_when_dwd_file_is_unreadable():
    settings = _settings()
    now = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)
    observations = [
        LocalObservation("wetterdaten-gl-fw-1", "Lufttemperatur", 20.0, datetime(2026, 6, 30, 8, tzinfo=timezone.utc)),
        LocalObservation("wetterdaten-gl-fw-1", "wind_speed", 2.0, datetime(2026, 6, 30, 8, tzinfo=timezone.utc)),
        LocalObservation("wetterdaten-gl-fw-1", "Niederschlag", 0.0, datetime(2026, 6, 30, 8, tzinfo=timezone.utc)),
    ]

    with (
        patch("weather_ai.chat._now_utc", return_value=now),
        patch("weather_ai.chat.WeatherStationCsvCache.observations_between", return_value=observations),
        patch("weather_ai.chat._summarize_dwd_history_between", side_effect=PermissionError("Datei gesperrt")),
        patch("weather_ai.chat.MosmixClient.fetch_forecasts", side_effect=AssertionError("live forecast not expected")),
    ):
        answer = ChatService(settings).answer("wie war das wetter gestern")

    assert "gestern" in answer
    assert "Lokale Daten (alle Stationen):" in answer
    assert "Temperatur: im Mittel **20" in answer
    assert "DWD-Historie: Fuer diesen Zeitraum fehlen verwertbare Daten." in answer
    assert "DWD-Historie konnte nicht gelesen werden: Datei gesperrt" in answer
    assert "naechsten verfuegbaren Prognosezeiten" not in answer


def test_chat_answers_specific_date_as_day_history():
    settings = replace(_settings(), local_cache_path=Path("tests/fixtures/local_weather_multi_station.csv"))
    now = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)

    with (
        patch("weather_ai.chat._now_utc", return_value=now),
        patch("weather_ai.chat._summarize_dwd_history_between", return_value={"points": 0}),
        patch("weather_ai.chat.MosmixClient.fetch_forecasts", side_effect=AssertionError("live forecast not expected")),
    ):
        answer = ChatService(settings).answer("wie war das wetter am 27.06.2026")

    assert "den 27.06.2026" in answer
    assert "Lokale Daten (alle Stationen):" in answer
    assert "Temperatur: im Mittel **25" in answer
    assert "Fuer das Jahr 2026" not in answer
    assert "So weit nach vorne" not in answer
    assert "naechsten verfuegbaren Prognosezeiten" not in answer


def test_chat_uses_all_stations_when_no_station_is_named():
    settings = replace(_settings(), local_cache_path=Path("tests/fixtures/local_weather_multi_station.csv"))
    now = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)

    with (
        patch("weather_ai.chat._now_utc", return_value=now),
        patch("weather_ai.chat._summarize_dwd_history_between", return_value={"points": 0}),
    ):
        answer = ChatService(settings).answer("wie war das wetter gestern")

    assert "Lokale Daten (alle Stationen):" in answer
    assert "Temperatur: im Mittel **23" in answer
    assert "Station Mitte" not in answer


def test_chat_uses_only_named_station():
    settings = replace(_settings(), local_cache_path=Path("tests/fixtures/local_weather_multi_station.csv"))
    now = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)

    with (
        patch("weather_ai.chat._now_utc", return_value=now),
        patch("weather_ai.chat._summarize_dwd_history_between", return_value={"points": 0}),
    ):
        answer = ChatService(settings).answer("wie war das wetter gestern an Station Mitte")

    assert "Lokale Daten (Station Mitte):" in answer
    assert "Temperatur: im Mittel **28" in answer
    assert "**23" not in answer


def test_chat_asks_when_station_alias_is_ambiguous():
    settings = _settings()
    rows = [
        {"time": "2026-06-30T08:00:00+00:00", "measurement": "wetterdaten-gl-a", "field": "Lufttemperatur", "value": "20"},
        {"time": "2026-06-30T08:00:00+00:00", "measurement": "wetterdaten-a", "field": "Lufttemperatur", "value": "22"},
    ]

    with patch("weather_ai.stations.read_cache_rows", return_value=rows):
        answer = ChatService(settings).answer("wie war das wetter an Station A")

    assert "nicht eindeutig" in answer
    assert "wetterdaten-gl-a" in answer
    assert "wetterdaten-a" in answer


def test_chat_limits_future_weather_questions_to_six_months():
    settings = _settings()
    now = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)

    with patch("weather_ai.chat._now_utc", return_value=now):
        answer = ChatService(settings).answer("Wie wird das Wetter im Februar 2027?")

    assert "maximal sechs Monate" in answer
    assert "Fuer Februar 2027 wuerde ich sonst Werte erfinden" in answer
    assert "Temperatur: nicht verfuegbar." in answer
    assert "Wind: nicht verfuegbar." in answer
    assert "Niederschlag: nicht verfuegbar." in answer


def _settings() -> Settings:
    return Settings(
        influx_url="http://influx.example",
        influx_token="token",
        influx_org="org",
        influx_bucket="iot",
        local_measurement="wetterdaten-gl-fw-1",
        local_lat=None,
        local_lon=None,
        dwd_station_id="",
        dwd_base_url="https://dwd.example",
        mosmix_station_id="10513",
        mosmix_product="MOSMIX_L",
        mosmix_base_url="http://opendata.dwd.de",
        model_dir=Path(".tmp-test-models"),
        local_cache_path=Path("data/local_weather_history.csv"),
        local_cache_retention_days=1095,
        local_cache_sync_on_startup=False,
        dwd_cdc_base_url="http://opendata.dwd.de/climate_environment/CDC",
        dwd_historical_station_ids=["02667"],
        dwd_historical_resolution="10_minutes",
        dwd_historical_parameters=["air_temperature", "precipitation", "wind"],
        dwd_historical_retention_days=1095,
        dwd_data_path=Path("data/dwd_weather_data.csv"),
    )
