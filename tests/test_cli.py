from datetime import datetime, timezone
from pathlib import Path

from weather_ai.cli import _cache_result_payload
from weather_ai.local_cache import CacheSyncResult


def test_cache_result_payload_includes_warning_when_present():
    result = CacheSyncResult(
        path=Path("data/local_weather_history.csv"),
        existing_rows=10,
        fetched_rows=0,
        written_rows=10,
        cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc),
        started_at=datetime(2026, 6, 25, tzinfo=timezone.utc),
        warning="InfluxDB ist nicht erreichbar.",
    )

    payload = _cache_result_payload(result)

    assert payload["warning"] == "InfluxDB ist nicht erreichbar."
