from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .dwd_data import DWD_DATA_COLUMNS, merge_dwd_rows, read_dwd_rows, write_dwd_rows
from .models import ForecastPoint


FORECAST_COLUMNS = DWD_DATA_COLUMNS


@dataclass(frozen=True)
class ForecastArchiveResult:
    path: Path
    fetched: int
    written_rows: int
    at: datetime


class ForecastCsvArchive:
    def __init__(self, settings: Settings):
        self.path = settings.dwd_data_path

    def append(self, forecasts: list[ForecastPoint]) -> ForecastArchiveResult:
        existing = read_dwd_rows(self.path)
        forecast_rows = [row for row in existing if row.get("kind") == "forecast"]
        preserved_rows = [row for row in existing if row.get("kind") != "forecast"]
        merged_forecasts = merge_forecast_rows(forecast_rows, [forecast_to_row(item) for item in forecasts])
        merged = merge_dwd_rows([*preserved_rows, *merged_forecasts])
        write_dwd_rows(self.path, merged)
        return ForecastArchiveResult(
            path=self.path,
            fetched=len(forecasts),
            written_rows=len(merged_forecasts),
            at=datetime.now(timezone.utc),
        )

    def read(self) -> list[ForecastPoint]:
        return [row_to_forecast(row) for row in read_forecast_rows(self.path)]


def read_forecast_rows(path: Path) -> list[dict[str, str]]:
    return [row for row in read_dwd_rows(path) if row.get("kind") == "forecast" and row.get("valid_at")]


def write_forecast_rows(path: Path, rows: list[dict[str, str]]) -> None:
    write_dwd_rows(path, merge_dwd_rows(rows))


def merge_forecast_rows(existing_rows: list[dict[str, str]], new_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: dict[tuple[str, str, str, str, str], dict[str, str]] = {}
    for row in [*existing_rows, *new_rows]:
        key = (row["source"], row["station_id"], row["field"], row["issued_at"], row["valid_at"])
        merged[key] = {column: row.get(column, "") for column in FORECAST_COLUMNS}
    return sorted(merged.values(), key=lambda row: (row["issued_at"], row["valid_at"], row["station_id"], row["field"]))


def forecast_to_row(forecast: ForecastPoint) -> dict[str, str]:
    return {
        "kind": "forecast",
        "time": forecast.valid_at.isoformat(),
        "source": forecast.source,
        "station_id": forecast.station_id,
        "dataset": "forecast",
        "field": forecast.variable,
        "value": f"{forecast.value:g}",
        "quality": "",
        "source_url": "",
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
        variable=row.get("field", ""),
        value=float(row.get("value", "nan")),
        issued_at=_parse_time(row.get("issued_at", "")),
        valid_at=_parse_time(row.get("valid_at", "")),
        horizon_hours=float(row.get("horizon_hours", "0")),
        unit=row.get("unit", ""),
        raw_name=row.get("raw_name", ""),
    )


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
