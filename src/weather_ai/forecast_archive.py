from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import csv
import shutil

from .config import Settings
from .models import ForecastPoint


FORECAST_COLUMNS = [
    "source",
    "station_id",
    "variable",
    "value",
    "issued_at",
    "valid_at",
    "horizon_hours",
    "unit",
    "raw_name",
]


@dataclass(frozen=True)
class ForecastArchiveResult:
    path: Path
    fetched: int
    written_rows: int
    at: datetime


class ForecastCsvArchive:
    def __init__(self, settings: Settings):
        self.path = settings.forecast_archive_path

    def append(self, forecasts: list[ForecastPoint]) -> ForecastArchiveResult:
        existing = read_forecast_rows(self.path)
        merged = merge_forecast_rows(existing, [forecast_to_row(item) for item in forecasts])
        write_forecast_rows(self.path, merged)
        return ForecastArchiveResult(
            path=self.path,
            fetched=len(forecasts),
            written_rows=len(merged),
            at=datetime.now(timezone.utc),
        )

    def read(self) -> list[ForecastPoint]:
        return [row_to_forecast(row) for row in read_forecast_rows(self.path)]


def read_forecast_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            {column: row.get(column, "") for column in FORECAST_COLUMNS}
            for row in reader
            if row.get("valid_at") and row.get("variable")
        ]


def write_forecast_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FORECAST_COLUMNS)
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


def merge_forecast_rows(existing_rows: list[dict[str, str]], new_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: dict[tuple[str, str, str, str, str], dict[str, str]] = {}
    for row in [*existing_rows, *new_rows]:
        key = (row["source"], row["station_id"], row["variable"], row["issued_at"], row["valid_at"])
        merged[key] = {column: row.get(column, "") for column in FORECAST_COLUMNS}
    return sorted(merged.values(), key=lambda row: (row["issued_at"], row["valid_at"], row["station_id"], row["variable"]))


def forecast_to_row(forecast: ForecastPoint) -> dict[str, str]:
    return {
        "source": forecast.source,
        "station_id": forecast.station_id,
        "variable": forecast.variable,
        "value": f"{forecast.value:g}",
        "issued_at": forecast.issued_at.isoformat(),
        "valid_at": forecast.valid_at.isoformat(),
        "horizon_hours": f"{forecast.horizon_hours:g}",
        "unit": forecast.unit,
        "raw_name": forecast.raw_name,
    }


def row_to_forecast(row: dict[str, str]) -> ForecastPoint:
    return ForecastPoint(
        source=row.get("source", ""),
        station_id=row.get("station_id", ""),
        variable=row.get("variable", ""),
        value=float(row.get("value", "nan")),
        issued_at=_parse_time(row.get("issued_at", "")),
        valid_at=_parse_time(row.get("valid_at", "")),
        horizon_hours=float(row.get("horizon_hours", "0")),
        unit=row.get("unit", ""),
        raw_name=row.get("raw_name", ""),
    )


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
