from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv
import shutil

from .config import Settings
from .influx import InfluxClient, _parse_time


CACHE_COLUMNS = ["time", "measurement", "field", "value"]


@dataclass(frozen=True)
class CacheSyncResult:
    path: Path
    existing_rows: int
    fetched_rows: int
    written_rows: int
    cutoff: datetime
    started_at: datetime


class WeatherStationCsvCache:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.local_cache_path

    def sync(self) -> CacheSyncResult:
        started_at = datetime.now(timezone.utc)
        cutoff = started_at - timedelta(days=self.settings.local_cache_retention_days)
        existing = read_cache_rows(self.path)
        pruned = prune_rows(existing, cutoff)
        latest = latest_row_time(pruned)
        fetch_start = latest or cutoff
        fetched = InfluxClient(self.settings).weather_station_rows_since(fetch_start)
        merged = merge_rows(pruned, fetched, cutoff)
        write_cache_rows(self.path, merged)
        return CacheSyncResult(
            path=self.path,
            existing_rows=len(existing),
            fetched_rows=len(fetched),
            written_rows=len(merged),
            cutoff=cutoff,
            started_at=started_at,
        )


def sync_cache_on_startup(settings: Settings) -> CacheSyncResult | None:
    if not settings.local_cache_sync_on_startup:
        return None
    if not settings.has_influx_credentials:
        return None
    return WeatherStationCsvCache(settings).sync()


def read_cache_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            {column: row.get(column, "") for column in CACHE_COLUMNS}
            for row in reader
            if row.get("time") and row.get("measurement") and row.get("field")
        ]


def write_cache_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CACHE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    try:
        temp_path.replace(path)
    except PermissionError:
        shutil.copyfile(temp_path, path)
        try:
            temp_path.unlink(missing_ok=True)
        except PermissionError:
            pass


def prune_rows(rows: list[dict[str, str]], cutoff: datetime) -> list[dict[str, str]]:
    return [row for row in rows if _row_time(row) and _row_time(row) >= cutoff]


def latest_row_time(rows: list[dict[str, str]]) -> datetime | None:
    times = [_row_time(row) for row in rows]
    valid_times = [item for item in times if item is not None]
    return max(valid_times) if valid_times else None


def merge_rows(
    existing_rows: list[dict[str, str]],
    fetched_rows: list[dict[str, str]],
    cutoff: datetime,
) -> list[dict[str, str]]:
    merged: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in [*existing_rows, *normalize_influx_rows(fetched_rows)]:
        row_time = _row_time(row)
        if row_time is None or row_time < cutoff:
            continue
        key = (row["time"], row["measurement"], row["field"])
        merged[key] = {column: row.get(column, "") for column in CACHE_COLUMNS}
    return sorted(merged.values(), key=lambda row: (row["time"], row["measurement"], row["field"]))


def normalize_influx_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for row in rows:
        time_value = row.get("_time") or row.get("time")
        measurement = row.get("_measurement") or row.get("measurement")
        field = row.get("_field") or row.get("field")
        value = row.get("_value") or row.get("value") or ""
        if not time_value or not measurement or not field:
            continue
        normalized.append(
            {
                "time": _parse_time(time_value).isoformat(),
                "measurement": measurement,
                "field": field,
                "value": value,
            }
        )
    return normalized


def _row_time(row: dict[str, str]) -> datetime | None:
    value = row.get("time")
    if not value:
        return None
    try:
        return _parse_time(value)
    except ValueError:
        return None
