from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Iterable
import csv

from .config import Settings
from .models import ForecastPoint, MODEL_VARIABLES


PROFILE_COLUMNS = [
    "generated_at",
    "source",
    "target_date",
    "variable",
    "min_value",
    "max_value",
    "min_at",
    "max_at",
    "avg_value",
    "points",
    "issued_at",
]


@dataclass(frozen=True)
class DailyProfile:
    generated_at: datetime
    source: str
    target_date: date
    variable: str
    min_value: float
    max_value: float
    min_at: datetime
    max_at: datetime
    avg_value: float
    points: int
    issued_at: datetime


def daily_profile_path(settings: Settings) -> Path:
    return settings.dwd_data_path.with_name("daily_weather_profiles.csv")


def corrected_forecasts(forecasts: Iterable[ForecastPoint], model_dir: Path) -> list[ForecastPoint]:
    models = _load_models(model_dir)
    corrected: list[ForecastPoint] = []
    for forecast in forecasts:
        model = models.get(forecast.variable)
        if model is None:
            continue
        try:
            value = float(model.predict([[forecast.value, forecast.horizon_hours]])[0])
        except Exception:  # noqa: BLE001 - one bad model should not break raw DWD profiles.
            continue
        corrected.append(
            ForecastPoint(
                source="local-corrected",
                station_id=forecast.station_id,
                variable=forecast.variable,
                value=value,
                issued_at=forecast.issued_at,
                valid_at=forecast.valid_at,
                horizon_hours=forecast.horizon_hours,
                unit=forecast.unit,
                raw_name=forecast.raw_name,
            )
        )
    return corrected


def profiles_for_forecasts(
    forecasts: Iterable[ForecastPoint],
    *,
    source: str,
    generated_at: datetime | None = None,
) -> list[DailyProfile]:
    generated_at = generated_at or datetime.now(timezone.utc)
    grouped: dict[tuple[date, str], list[ForecastPoint]] = {}
    for forecast in forecasts:
        if forecast.variable not in MODEL_VARIABLES:
            continue
        key = (forecast.valid_at.date(), forecast.variable)
        grouped.setdefault(key, []).append(forecast)

    profiles: list[DailyProfile] = []
    for (target_date, variable), items in grouped.items():
        latest_issued_at = max(item.issued_at for item in items)
        latest_items = [item for item in items if item.issued_at == latest_issued_at]
        values = [item.value for item in latest_items]
        if not values:
            continue
        min_item = min(latest_items, key=lambda item: item.value)
        max_item = max(latest_items, key=lambda item: item.value)
        profiles.append(
            DailyProfile(
                generated_at=generated_at,
                source=source,
                target_date=target_date,
                variable=variable,
                min_value=float(min_item.value),
                max_value=float(max_item.value),
                min_at=min_item.valid_at,
                max_at=max_item.valid_at,
                avg_value=float(mean(values)),
                points=len(latest_items),
                issued_at=latest_issued_at,
            )
        )
    return sorted(profiles, key=lambda item: (item.target_date, item.source, item.variable))


def latest_profiles_for_date(path: Path, target_date: date) -> list[DailyProfile]:
    rows = [
        row
        for row in read_profile_rows(path)
        if row.get("target_date") == target_date.isoformat()
    ]
    profiles = [_row_to_profile(row) for row in rows]
    if not profiles:
        return []
    latest_by_key: dict[tuple[str, str], DailyProfile] = {}
    for profile in profiles:
        key = (profile.source, profile.variable)
        current = latest_by_key.get(key)
        if current is None or profile.generated_at > current.generated_at:
            latest_by_key[key] = profile
    return sorted(latest_by_key.values(), key=lambda item: (item.source, item.variable))


def append_profiles(path: Path, profiles: list[DailyProfile]) -> int:
    if not profiles:
        return 0
    existing = read_profile_rows(path)
    merged: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in [*existing, *[profile_to_row(profile) for profile in profiles]]:
        merged[_profile_key(row)] = {column: row.get(column, "") for column in PROFILE_COLUMNS}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PROFILE_COLUMNS)
        writer.writeheader()
        writer.writerows(sorted(merged.values(), key=lambda row: (row["target_date"], row["source"], row["variable"], row["issued_at"])))
    return len(profiles)


def read_profile_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            {column: row.get(column, "") for column in PROFILE_COLUMNS}
            for row in reader
            if row.get("target_date") and row.get("source") and row.get("variable")
        ]


def profile_to_row(profile: DailyProfile) -> dict[str, str]:
    return {
        "generated_at": profile.generated_at.isoformat(),
        "source": profile.source,
        "target_date": profile.target_date.isoformat(),
        "variable": profile.variable,
        "min_value": f"{profile.min_value:g}",
        "max_value": f"{profile.max_value:g}",
        "min_at": profile.min_at.isoformat(),
        "max_at": profile.max_at.isoformat(),
        "avg_value": f"{profile.avg_value:g}",
        "points": str(profile.points),
        "issued_at": profile.issued_at.isoformat(),
    }


def _profile_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        row.get("source", ""),
        row.get("target_date", ""),
        row.get("variable", ""),
        row.get("issued_at", ""),
    )


def _row_to_profile(row: dict[str, str]) -> DailyProfile:
    return DailyProfile(
        generated_at=_parse_datetime(row["generated_at"]),
        source=row["source"],
        target_date=date.fromisoformat(row["target_date"]),
        variable=row["variable"],
        min_value=float(row["min_value"]),
        max_value=float(row["max_value"]),
        min_at=_parse_datetime(row["min_at"]),
        max_at=_parse_datetime(row["max_at"]),
        avg_value=float(row["avg_value"]),
        points=int(row["points"]),
        issued_at=_parse_datetime(row["issued_at"]),
    )


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _load_models(model_dir: Path) -> dict[str, object]:
    if not model_dir.exists():
        return {}
    try:
        from joblib import load
    except Exception:  # noqa: BLE001
        return {}
    models: dict[str, object] = {}
    for variable in sorted(MODEL_VARIABLES):
        candidates = sorted(model_dir.glob(f"{variable}-*.joblib"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not candidates:
            continue
        try:
            models[variable] = load(candidates[0])
        except Exception:  # noqa: BLE001
            continue
    return models
