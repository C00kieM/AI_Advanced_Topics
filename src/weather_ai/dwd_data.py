from __future__ import annotations

from pathlib import Path
import csv


DWD_DATA_COLUMNS = [
    "kind",
    "time",
    "station_id",
    "dataset",
    "field",
    "value",
    "quality",
    "source_url",
    "source",
    "issued_at",
    "valid_at",
    "horizon_hours",
    "unit",
    "raw_name",
]


def read_dwd_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            {column: row.get(column, "") for column in DWD_DATA_COLUMNS}
            for row in reader
            if row.get("kind") and row.get("time") and row.get("station_id") and row.get("field")
        ]


def write_dwd_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DWD_DATA_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def merge_dwd_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: dict[tuple[str, ...], dict[str, str]] = {}
    for row in rows:
        normalized = {column: row.get(column, "") for column in DWD_DATA_COLUMNS}
        merged[_row_key(normalized)] = normalized
    return sorted(merged.values(), key=lambda row: (row["kind"], row["time"], row["station_id"], row["dataset"], row["field"]))


def _row_key(row: dict[str, str]) -> tuple[str, ...]:
    if row.get("kind") == "forecast":
        return (
            "forecast",
            row.get("source", ""),
            row.get("station_id", ""),
            row.get("field", ""),
            row.get("issued_at", ""),
            row.get("valid_at", ""),
        )
    return (
        row.get("kind", ""),
        row.get("time", ""),
        row.get("station_id", ""),
        row.get("dataset", ""),
        row.get("field", ""),
    )
