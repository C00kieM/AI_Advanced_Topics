from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from weather_ai.chat import ChatService
from weather_ai.config import Settings
from weather_ai.models import LocalObservation, StatusReport


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
        answer = ChatService(settings).answer("Wie wird das Wetter morgen?")

    assert "Datenlage:" in answer
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
        patch("weather_ai.chat.WeatherStationCsvCache.observations_between", return_value=[]),
        patch("weather_ai.chat._dwd_history_rows_between", return_value=rows),
        patch("weather_ai.chat.MosmixClient.fetch_forecasts", side_effect=AssertionError("live forecast not expected")),
    ):
        answer = ChatService(settings).answer("wie war das wetter im Januar 2026")

    assert "Januar 2026" in answer
    assert "DWD-CDC-Historie" in answer
    assert "Historische Auswertung:" in answer
    assert "DWD-Prognose:" not in answer
    assert "Temperatur: avg 3" in answer
    assert "Niederschlag: Summe 1.5 mm" in answer
