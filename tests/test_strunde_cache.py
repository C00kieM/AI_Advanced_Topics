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
    write_strunde_rows,
)
from weather_ai.sync_state import sync_state_path


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


def test_strunde_sync_keeps_existing_cache_when_influx_is_offline():
    cache_path = _runtime_path("strunde-offline-existing.csv")
    _clean_runtime_path(cache_path)
    write_strunde_rows(
        cache_path,
        [{"time": "2026-07-01T08:00:00+00:00", "measurement": "pegel-strunde", "field": "water_level_cm", "value": "42"}],
    )
    settings = replace(Settings.from_env(), strunde_cache_path=cache_path)

    with patch("weather_ai.strunde_cache.InfluxClient.strunde_level_rows_since", side_effect=InfluxError("offline")):
        result = StrundeLevelCsvCache(settings).sync(force=True)

    assert result.warning
    assert result.written_rows == 1
    assert read_strunde_rows(cache_path)[0]["value"] == "42"


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
