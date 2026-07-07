from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv

from .config import Settings
from .influx import InfluxClient, InfluxError, _parse_time
from .sync_state import attempted_today, read_sync_state, sync_state_path, write_sync_state


STRUNDE_COLUMNS = ["time", "measurement", "field", "value"]
RETENTION_DAYS = 365 * 3


@dataclass(frozen=True)
class StrundeLevelObservation:
    measurement: str
    field: str
    value_cm: float
    time: datetime


@dataclass(frozen=True)
class StrundeCacheSyncResult:
    path: Path
    existing_rows: int
    fetched_rows: int
    written_rows: int
    cutoff: datetime
    started_at: datetime
    warning: str | None = None
    skipped: bool = False
    reason: str | None = None


class StrundeLevelCsvCache:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.strunde_cache_path
        self.state_path = sync_state_path(self.path, "strunde-level")

    def sync(self, force: bool = False) -> StrundeCacheSyncResult:
        started_at = datetime.now(timezone.utc)
        cutoff = retention_cutoff(started_at, self.settings.strunde_cache_retention_days)
        state = read_sync_state(self.state_path)
        existing = read_strunde_rows(self.path)
        scoped_existing = _configured_rows(existing, self.settings)
        pruned = prune_strunde_rows(scoped_existing, cutoff)
        if (
            not force
            and pruned
            and attempted_today(state, started_at)
            and state.get("status") == "success"
            and _state_count(state) > 0
        ):
            if len(pruned) != len(scoped_existing) or len(existing) != len(scoped_existing):
                write_strunde_rows(self.path, pruned)
                write_sync_state(
                    self.state_path,
                    attempted_at=started_at,
                    status="success",
                    details={"existing_rows": len(existing), "fetched_rows": 0, "written_rows": len(pruned), "pruned_rows": len(scoped_existing) - len(pruned)},
                )
            return StrundeCacheSyncResult(
                path=self.path,
                existing_rows=len(existing),
                fetched_rows=0,
                written_rows=len(pruned),
                cutoff=cutoff,
                started_at=started_at,
                skipped=True,
                reason="Strunde-Pegel-Update uebersprungen: Heute wurde bereits synchronisiert.",
            )
        latest = latest_strunde_row_time(pruned)
        fetch_start = latest or cutoff
        try:
            fetched = InfluxClient(self.settings).strunde_level_rows_since(fetch_start)
        except InfluxError as exc:
            if not pruned:
                raise
            write_strunde_rows(self.path, pruned)
            write_sync_state(
                self.state_path,
                attempted_at=started_at,
                status="warning",
                details={"error": str(exc), "existing_rows": len(existing), "written_rows": len(pruned)},
            )
            return StrundeCacheSyncResult(
                path=self.path,
                existing_rows=len(existing),
                fetched_rows=0,
                written_rows=len(pruned),
                cutoff=cutoff,
                started_at=started_at,
                warning=(
                    "InfluxDB ist nicht erreichbar; der bestehende Strunde-Pegelcache "
                    f"bleibt aktiv. Ursache: {exc}"
                ),
            )
        merged = merge_strunde_rows(pruned, fetched, cutoff)
        write_strunde_rows(self.path, merged)
        if not merged:
            warning = (
                "Keine Strunde-Pegelwerte fuer die konfigurierte InfluxDB-Serie gefunden: "
                f"measurement={self.settings.strunde_measurement}, field={self.settings.strunde_level_field}."
            )
            write_sync_state(
                self.state_path,
                attempted_at=started_at,
                status="warning",
                details={"existing_rows": len(existing), "fetched_rows": len(fetched), "written_rows": 0, "warning": warning},
            )
            return StrundeCacheSyncResult(
                path=self.path,
                existing_rows=len(existing),
                fetched_rows=len(fetched),
                written_rows=0,
                cutoff=cutoff,
                started_at=started_at,
                warning=warning,
            )
        write_sync_state(
            self.state_path,
            attempted_at=started_at,
            status="success",
            details={"existing_rows": len(existing), "fetched_rows": len(fetched), "written_rows": len(merged)},
        )
        return StrundeCacheSyncResult(
            path=self.path,
            existing_rows=len(existing),
            fetched_rows=len(fetched),
            written_rows=len(merged),
            cutoff=cutoff,
            started_at=started_at,
        )

    def observations_since(self, days: int) -> list[StrundeLevelObservation]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return self.observations_between(cutoff, datetime.now(timezone.utc))

    def observations_between(self, start: datetime, end: datetime) -> list[StrundeLevelObservation]:
        observations: list[StrundeLevelObservation] = []
        for row in read_strunde_rows(self.path):
            if row.get("measurement") != self.settings.strunde_measurement or row.get("field") != self.settings.strunde_level_field:
                continue
            row_time = _row_time(row)
            if row_time is None or row_time < start or row_time >= end:
                continue
            value = _to_float(row.get("value", ""))
            if value is None or not _is_valid_level_cm(value):
                continue
            observations.append(
                StrundeLevelObservation(
                    measurement=row.get("measurement", self.settings.strunde_measurement),
                    field=row.get("field", self.settings.strunde_level_field),
                    value_cm=value,
                    time=row_time,
                )
            )
        return observations

    def latest(self) -> StrundeLevelObservation | None:
        observations = self.observations_since(days=RETENTION_DAYS)
        return max(observations, key=lambda item: item.time) if observations else None


def read_strunde_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            {column: row.get(column, "") for column in STRUNDE_COLUMNS}
            for row in reader
            if row.get("time") and row.get("measurement") and row.get("field")
        ]


def write_strunde_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=STRUNDE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def normalize_strunde_influx_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
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


def prune_strunde_rows(rows: list[dict[str, str]], cutoff: datetime) -> list[dict[str, str]]:
    return [row for row in rows if _row_time(row) and _row_time(row) >= cutoff]


def latest_strunde_row_time(rows: list[dict[str, str]]) -> datetime | None:
    times = [_row_time(row) for row in rows]
    valid_times = [item for item in times if item is not None]
    return max(valid_times) if valid_times else None


def merge_strunde_rows(
    existing_rows: list[dict[str, str]],
    fetched_rows: list[dict[str, str]],
    cutoff: datetime,
) -> list[dict[str, str]]:
    merged: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in [*existing_rows, *normalize_strunde_influx_rows(fetched_rows)]:
        row_time = _row_time(row)
        if row_time is None or row_time < cutoff:
            continue
        key = (row["time"], row["measurement"], row["field"])
        merged[key] = {column: row.get(column, "") for column in STRUNDE_COLUMNS}
    return sorted(merged.values(), key=lambda row: (row["time"], row["measurement"], row["field"]))


def _configured_rows(rows: list[dict[str, str]], settings: Settings) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if row.get("measurement") == settings.strunde_measurement
        and row.get("field") == settings.strunde_level_field
    ]


def retention_cutoff(reference: datetime, configured_days: int) -> datetime:
    reference_utc = reference.astimezone(timezone.utc)
    cutoff_date = reference_utc.date() - timedelta(days=RETENTION_DAYS)
    return datetime(cutoff_date.year, cutoff_date.month, cutoff_date.day, tzinfo=timezone.utc)


def _state_count(state: dict) -> int:
    details = state.get("details") if isinstance(state.get("details"), dict) else {}
    value = details.get("written_rows") or details.get("existing_rows") or 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _row_time(row: dict[str, str]) -> datetime | None:
    value = row.get("time")
    if not value:
        return None
    try:
        return _parse_time(value)
    except ValueError:
        return None


def _to_float(value: str) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _is_valid_level_cm(value: float) -> bool:
    return 0.0 <= value <= 1000.0
