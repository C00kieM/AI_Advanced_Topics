from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from weather_ai.influx import InfluxError
from weather_ai.local_cache import latest_row_time, merge_rows, normalize_influx_rows, prune_rows, retention_cutoff
from weather_ai.local_cache import WeatherStationCsvCache
from weather_ai.config import Settings


def test_normalize_influx_rows_maps_weather_values():
    rows = normalize_influx_rows(
        [
            {
                "_time": "2026-05-20T09:24:56Z",
                "_measurement": "wetterdaten-gl-fw-2",
                "_field": "Lufttemperatur",
                "_value": "12.6",
            }
        ]
    )

    assert rows == [
        {
            "time": "2026-05-20T09:24:56+00:00",
            "measurement": "wetterdaten-gl-fw-2",
            "field": "Lufttemperatur",
            "value": "12.6",
        }
    ]


def test_merge_rows_prunes_old_rows_and_deduplicates_newer_values():
    cutoff = datetime(2023, 5, 20, tzinfo=timezone.utc)
    existing = [
        {"time": "2022-01-01T00:00:00+00:00", "measurement": "wetterdaten-a", "field": "x", "value": "old"},
        {"time": "2026-05-20T09:00:00+00:00", "measurement": "wetterdaten-a", "field": "x", "value": "1"},
    ]
    fetched = [
        {
            "_time": "2026-05-20T09:00:00Z",
            "_measurement": "wetterdaten-a",
            "_field": "x",
            "_value": "2",
        },
        {
            "_time": "2026-05-20T09:15:00Z",
            "_measurement": "wetterdaten-b",
            "_field": "y",
            "_value": "3",
        },
    ]

    merged = merge_rows(existing, fetched, cutoff)

    assert len(merged) == 2
    assert merged[0]["value"] == "2"
    assert latest_row_time(merged) == datetime(2026, 5, 20, 9, 15, tzinfo=timezone.utc)
    assert prune_rows(existing, cutoff) == [existing[1]]


def test_retention_cutoff_is_aligned_to_full_utc_day():
    reference = datetime(2026, 7, 6, 12, 34, 56, tzinfo=timezone.utc)

    assert retention_cutoff(reference, 30) == datetime(2023, 7, 7, tzinfo=timezone.utc)
    assert retention_cutoff(reference, 3650) == datetime(2023, 7, 7, tzinfo=timezone.utc)


def test_cache_observations_since_reads_fixed_csv():
    settings = replace(Settings.from_env(), local_cache_path=Path("tests/fixtures/local_weather_history.csv"))
    cache = WeatherStationCsvCache(settings)

    observations = cache.observations_since(days=3650)

    assert len(observations) == 1
    assert observations[0].measurement == "wetterdaten-gl-fw-2"
    assert observations[0].value == 12.6


def test_cache_observations_between_filters_date_range_and_fields():
    settings = replace(Settings.from_env(), local_cache_path=Path("unused.csv"))
    rows = [
        {
            "time": "2026-01-01T00:00:00+00:00",
            "measurement": "station",
            "field": "Lufttemperatur",
            "value": "2.5",
        },
        {
            "time": "2026-02-01T00:00:00+00:00",
            "measurement": "station",
            "field": "Lufttemperatur",
            "value": "8.0",
        },
        {
            "time": "2026-01-15T00:00:00+00:00",
            "measurement": "station",
            "field": "Luftdruck",
            "value": "1000",
        },
    ]

    with patch("weather_ai.local_cache.read_cache_rows", return_value=rows):
        observations = WeatherStationCsvCache(settings).observations_between(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 2, 1, tzinfo=timezone.utc),
            fields={"Lufttemperatur"},
        )

    assert len(observations) == 1
    assert observations[0].value == 2.5


def test_cache_observations_between_filters_measurements():
    settings = replace(Settings.from_env(), local_cache_path=Path("unused.csv"))
    rows = [
        {
            "time": "2026-01-01T00:00:00+00:00",
            "measurement": "target",
            "field": "Lufttemperatur",
            "value": "2.5",
        },
        {
            "time": "2026-01-01T00:00:00+00:00",
            "measurement": "other",
            "field": "Lufttemperatur",
            "value": "8.0",
        },
    ]

    with patch("weather_ai.local_cache.read_cache_rows", return_value=rows):
        observations = WeatherStationCsvCache(settings).observations_between(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 2, tzinfo=timezone.utc),
            measurements={"target"},
        )

    assert len(observations) == 1
    assert observations[0].measurement == "target"


def test_cache_sync_keeps_existing_cache_when_influx_is_unreachable():
    existing_rows = [
        {
            "time": datetime.now(timezone.utc).isoformat(),
            "measurement": "wetterdaten-gl-fw-2",
            "field": "Lufttemperatur",
            "value": "12.6",
        }
    ]
    settings = replace(Settings.from_env(), local_cache_path=Path("unused.csv"), local_cache_retention_days=30)

    with (
        patch("weather_ai.local_cache.read_cache_rows", return_value=existing_rows),
        patch("weather_ai.local_cache.write_cache_rows") as write_cache,
        patch("weather_ai.local_cache.write_sync_state"),
        patch(
            "weather_ai.local_cache.InfluxClient.weather_station_rows_since",
            side_effect=InfluxError("InfluxDB query failed: timeout"),
        ),
    ):
        result = WeatherStationCsvCache(settings).sync()

    assert result.fetched_rows == 0
    assert result.written_rows == 1
    assert result.warning is not None
    assert "bestehende lokale Cache bleibt aktiv" in result.warning
    write_cache.assert_called_once_with(settings.local_cache_path, existing_rows)


def test_cache_sync_skips_network_when_already_attempted_today():
    existing_rows = [
        {
            "time": datetime.now(timezone.utc).isoformat(),
            "measurement": "wetterdaten-gl-fw-2",
            "field": "Lufttemperatur",
            "value": "12.6",
        }
    ]
    settings = replace(Settings.from_env(), local_cache_path=Path("unused.csv"), local_cache_retention_days=30)

    with (
        patch(
            "weather_ai.local_cache.read_sync_state",
            return_value={
                "attempted_date": datetime.now(timezone.utc).date().isoformat(),
                "status": "success",
                "details": {"written_rows": 1},
            },
        ),
        patch("weather_ai.local_cache.read_cache_rows", return_value=existing_rows),
        patch("weather_ai.local_cache.InfluxClient.weather_station_rows_since") as fetch_rows,
    ):
        result = WeatherStationCsvCache(settings).sync()

    assert result.skipped is True
    assert result.fetched_rows == 0
    assert "Heute wurde bereits synchronisiert" in (result.reason or "")
    fetch_rows.assert_not_called()


def test_cache_sync_prunes_even_when_already_attempted_today():
    existing_rows = [
        {"time": "2022-01-01T00:00:00+00:00", "measurement": "old", "field": "Lufttemperatur", "value": "1"},
        {"time": datetime.now(timezone.utc).isoformat(), "measurement": "new", "field": "Lufttemperatur", "value": "2"},
    ]
    settings = replace(Settings.from_env(), local_cache_path=Path("unused.csv"))

    with (
        patch(
            "weather_ai.local_cache.read_sync_state",
            return_value={
                "attempted_date": datetime.now(timezone.utc).date().isoformat(),
                "status": "success",
                "details": {"written_rows": 2},
            },
        ),
        patch("weather_ai.local_cache.read_cache_rows", return_value=existing_rows),
        patch("weather_ai.local_cache.write_cache_rows") as write_cache,
        patch("weather_ai.local_cache.write_sync_state"),
        patch("weather_ai.local_cache.InfluxClient.weather_station_rows_since") as fetch_rows,
    ):
        result = WeatherStationCsvCache(settings).sync()

    assert result.skipped is True
    assert result.written_rows == 1
    write_cache.assert_called_once_with(settings.local_cache_path, [existing_rows[1]])
    fetch_rows.assert_not_called()


def test_cache_sync_fails_without_existing_cache_when_influx_is_unreachable():
    settings = replace(Settings.from_env(), local_cache_path=Path("unused.csv"), local_cache_retention_days=30)

    with (
        patch("weather_ai.local_cache.read_cache_rows", return_value=[]),
        patch(
            "weather_ai.local_cache.InfluxClient.weather_station_rows_since",
            side_effect=InfluxError("InfluxDB query failed: timeout"),
        ),
    ):
        with pytest.raises(InfluxError):
            WeatherStationCsvCache(settings).sync()
