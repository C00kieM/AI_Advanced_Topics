from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from weather_ai.config import Settings
from weather_ai.influx import InfluxError
from weather_ai.strunde_cache import (
    StrundeLevelCsvCache,
    merge_strunde_rows,
    normalize_strunde_influx_rows,
    read_strunde_rows,
    retention_cutoff,
    write_strunde_rows,
)
from weather_ai.sync_state import sync_state_path
from weather_ai.sync_state import write_sync_state


def test_normalize_strunde_influx_rows_maps_influx_columns():
    rows = normalize_strunde_influx_rows(
        [
            {
                "_time": "2026-07-01T08:00:00Z",
                "_measurement": "pegel-strunde",
                "_field": "water_level_cm",
                "_value": "42.5",
            }
        ]
    )

    assert rows == [
        {
            "time": "2026-07-01T08:00:00+00:00",
            "measurement": "pegel-strunde",
            "field": "water_level_cm",
            "value": "42.5",
        }
    ]


def test_merge_strunde_rows_prunes_and_deduplicates():
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    merged = merge_strunde_rows(
        [
            {"time": "2025-01-01T00:00:00+00:00", "measurement": "pegel-strunde", "field": "water_level_cm", "value": "10"},
            {"time": "2026-07-01T08:00:00+00:00", "measurement": "pegel-strunde", "field": "water_level_cm", "value": "41"},
        ],
        [
            {"_time": "2026-07-01T08:00:00+00:00", "_measurement": "pegel-strunde", "_field": "water_level_cm", "_value": "42"},
            {"_time": "2026-07-01T09:00:00+00:00", "_measurement": "pegel-strunde", "_field": "water_level_cm", "_value": "43"},
        ],
        cutoff,
    )

    assert [row["value"] for row in merged] == ["42", "43"]


def test_strunde_retention_cutoff_is_aligned_to_full_utc_day():
    reference = datetime(2026, 7, 6, 12, 34, 56, tzinfo=timezone.utc)

    assert retention_cutoff(reference, 30) == datetime(2023, 7, 7, tzinfo=timezone.utc)
    assert retention_cutoff(reference, 3650) == datetime(2023, 7, 7, tzinfo=timezone.utc)


def test_strunde_sync_keeps_existing_cache_when_influx_is_offline():
    cache_path = _runtime_path("strunde-offline-existing.csv")
    _clean_runtime_path(cache_path)
    settings = replace(Settings.from_env(), strunde_cache_path=cache_path)
    write_strunde_rows(
        cache_path,
        [
            {
                "time": "2026-07-01T08:00:00+00:00",
                "measurement": settings.strunde_measurement,
                "field": settings.strunde_level_field,
                "value": "42",
            }
        ],
    )

    with patch("weather_ai.strunde_cache.InfluxClient.strunde_level_rows_since", side_effect=InfluxError("offline")):
        result = StrundeLevelCsvCache(settings).sync(force=True)

    assert result.warning
    assert result.written_rows == 1
    assert read_strunde_rows(cache_path)[0]["value"] == "42"


def test_strunde_sync_filters_old_measurement_and_retries_warning_state():
    cache_path = _runtime_path("strunde-filter-and-retry.csv")
    _clean_runtime_path(cache_path)
    write_strunde_rows(
        cache_path,
        [{"time": "2026-07-01T08:00:00+00:00", "measurement": "old", "field": "old_field", "value": "999"}],
    )
    settings = replace(
        Settings.from_env(),
        strunde_cache_path=cache_path,
        strunde_measurement="strundepegel-gronau",
        strunde_level_field="pegelstandgronau",
    )
    write_sync_state(
        sync_state_path(cache_path, "strunde-level"),
        attempted_at=datetime.now(timezone.utc),
        status="warning",
        details={"written_rows": 1},
    )

    with patch(
        "weather_ai.strunde_cache.InfluxClient.strunde_level_rows_since",
        return_value=[
            {
                "_time": "2026-07-01T09:00:00+00:00",
                "_measurement": "strundepegel-gronau",
                "_field": "pegelstandgronau",
                "_value": "42",
            }
        ],
    ) as query:
        result = StrundeLevelCsvCache(settings).sync()

    assert result.skipped is False
    assert result.written_rows == 1
    query.assert_called_once()
    rows = read_strunde_rows(cache_path)
    assert rows == [
        {
            "time": "2026-07-01T09:00:00+00:00",
            "measurement": "strundepegel-gronau",
            "field": "pegelstandgronau",
            "value": "42",
        }
    ]


def test_strunde_sync_warns_when_query_returns_no_rows_for_empty_cache():
    cache_path = _runtime_path("strunde-empty-query.csv")
    _clean_runtime_path(cache_path)
    settings = replace(Settings.from_env(), strunde_cache_path=cache_path)

    with patch("weather_ai.strunde_cache.InfluxClient.strunde_level_rows_since", return_value=[]):
        result = StrundeLevelCsvCache(settings).sync(force=True)

    assert result.warning
    assert result.written_rows == 0


def test_strunde_sync_prunes_even_when_already_attempted_today():
    cache_path = _runtime_path("strunde-skip-prune.csv")
    _clean_runtime_path(cache_path)
    settings = replace(Settings.from_env(), strunde_cache_path=cache_path)
    write_strunde_rows(
        cache_path,
        [
            {"time": "2022-01-01T00:00:00+00:00", "measurement": settings.strunde_measurement, "field": settings.strunde_level_field, "value": "10"},
            {"time": datetime.now(timezone.utc).isoformat(), "measurement": settings.strunde_measurement, "field": settings.strunde_level_field, "value": "42"},
            {"time": datetime.now(timezone.utc).isoformat(), "measurement": "old", "field": "old", "value": "999"},
        ],
    )
    write_sync_state(
        sync_state_path(cache_path, "strunde-level"),
        attempted_at=datetime.now(timezone.utc),
        status="success",
        details={"written_rows": 3},
    )

    with patch("weather_ai.strunde_cache.InfluxClient.strunde_level_rows_since") as query:
        result = StrundeLevelCsvCache(settings).sync()

    assert result.skipped is True
    assert result.written_rows == 1
    rows = read_strunde_rows(cache_path)
    assert rows == [
        {
            "time": rows[0]["time"],
            "measurement": settings.strunde_measurement,
            "field": settings.strunde_level_field,
            "value": "42",
        }
    ]
    query.assert_not_called()


def test_strunde_sync_raises_when_influx_is_offline_and_cache_is_empty():
    cache_path = _runtime_path("strunde-offline-missing.csv")
    _clean_runtime_path(cache_path)
    settings = replace(Settings.from_env(), strunde_cache_path=cache_path)

    with (
        patch("weather_ai.strunde_cache.InfluxClient.strunde_level_rows_since", side_effect=InfluxError("offline")),
        pytest.raises(InfluxError),
    ):
        StrundeLevelCsvCache(settings).sync(force=True)
    _clean_runtime_path(cache_path)


def _runtime_path(name: str) -> Path:
    path = Path(".test-runtime") / name
    path.parent.mkdir(exist_ok=True)
    return path


def _clean_runtime_path(path: Path) -> None:
    if path.exists():
        try:
            path.unlink()
        except PermissionError:
            pass
    state_path = sync_state_path(path, "strunde-level")
    if state_path.exists():
        try:
            state_path.unlink()
        except PermissionError:
            pass
