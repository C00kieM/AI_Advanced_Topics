from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any
import csv

from .comparison import match_forecasts_to_observations, summarize_comparisons
from .config import Settings
from .diagnostics import build_status
from .forecast_archive import ForecastCsvArchive, row_to_forecast
from .local_cache import WeatherStationCsvCache, read_cache_rows
from .models import ForecastPoint, LOCAL_FIELD_MAP, MODEL_VARIABLES, StatusReport
from .stations import scoped_model_dir
from .strunde_cache import read_strunde_rows


TRAINING_MIN_POINTS = 24
_DWD_CACHE: dict[tuple[str, int, float], tuple[dict[str, Any], list[ForecastPoint]]] = {}


def build_gui_status(settings: Settings, live: bool = True, deep: bool = False) -> dict[str, Any]:
    report = _safe_status_report(settings) if live else StatusReport()
    local_cache = summarize_local_cache(settings.local_cache_path, selected_measurement=settings.local_measurement)
    strunde_cache = summarize_strunde_cache(settings.strunde_cache_path)
    dwd_data, forecasts = summarize_dwd_data(settings.dwd_data_path, deep=deep)
    comparison = summarize_cached_comparison(settings, forecasts)
    models = summarize_models(scoped_model_dir(settings), comparison["counts"])
    newest_local = _newest_local_time(report)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "influx_url": settings.influx_url,
            "influx_org": settings.influx_org,
            "influx_bucket": settings.influx_bucket,
            "influx_token": "gesetzt" if settings.influx_token else "fehlt",
            "local_measurement": settings.local_measurement,
            "dwd_station_id": settings.dwd_station_id,
            "mosmix_station_id": settings.mosmix_station_id,
            "mosmix_product": settings.mosmix_product,
            "dwd_data_path": str(settings.dwd_data_path),
            "local_cache_path": str(settings.local_cache_path),
            "strunde_cache_path": str(settings.strunde_cache_path),
            "strunde_measurement": settings.strunde_measurement,
            "strunde_level_field": settings.strunde_level_field,
        },
        "live": {
            "checked": live,
            "influx_ok": report.influx_ok,
            "dwd_ok": report.dwd_ok,
            "local_stale": report.is_local_stale,
            "latest_local_at": newest_local.isoformat() if newest_local else None,
            "warnings": report.warnings,
            "local_latest": [_jsonable(asdict(item)) for item in report.local_latest],
            "candidate_measurements": {
                measurement: latest.isoformat() if latest else None
                for measurement, latest in report.candidate_measurements.items()
            },
        },
        "local_cache": local_cache,
        "strunde_cache": strunde_cache,
        "dwd_data": dwd_data,
        "comparison": comparison,
        "models": models,
    }


def summarize_local_cache(path: Path, selected_measurement: str | None = None) -> dict[str, Any]:
    rows = read_cache_rows(path)
    measurement_counts = Counter(row["measurement"] for row in rows)
    field_counts = Counter(row["field"] for row in rows)
    times = [_parse_time(row.get("time", "")) for row in rows]
    valid_times = [item for item in times if item is not None]
    selected_rows = [row for row in rows if row.get("measurement") == selected_measurement] if selected_measurement else []
    selected_times = [_parse_time(row.get("time", "")) for row in selected_rows]
    selected_valid_times = [item for item in selected_times if item is not None]
    active_latest = max(selected_valid_times) if selected_valid_times else None
    return {
        "path": str(path),
        "exists": path.exists(),
        "rows": len(rows),
        "min_time": min(valid_times).isoformat() if valid_times else None,
        "max_time": max(valid_times).isoformat() if valid_times else None,
        "last_modified": _mtime(path),
        "measurements": dict(measurement_counts.most_common()),
        "fields": dict(field_counts.most_common()),
        "selected_measurement": selected_measurement,
        "selected_rows": len(selected_rows),
        "selected_min_time": min(selected_valid_times).isoformat() if selected_valid_times else None,
        "selected_max_time": active_latest.isoformat() if active_latest else None,
        "stale": _is_stale(active_latest),
    }


def summarize_dwd_data(path: Path, deep: bool = False) -> tuple[dict[str, Any], list[ForecastPoint] | None]:
    summary: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "deep": deep,
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "rows": 0,
        "observation_rows": 0,
        "forecast_rows": 0,
        "min_time": None,
        "max_time": None,
        "observation_min_time": None,
        "observation_max_time": None,
        "min_valid_at": None,
        "max_valid_at": None,
        "datasets": {},
        "fields": {},
        "last_modified": _mtime(path),
    }
    if not path.exists():
        return summary, []

    cache_key = (str(path.resolve()), path.stat().st_size, path.stat().st_mtime)
    cached = _DWD_CACHE.get(cache_key)
    if cached:
        cached_summary, cached_forecasts = cached
        payload = dict(cached_summary)
        payload["deep"] = deep
        return payload, cached_forecasts

    datasets: Counter[str] = Counter()
    fields: Counter[str] = Counter()
    min_time: str | None = None
    max_time: str | None = None
    observation_min_time: str | None = None
    observation_max_time: str | None = None
    min_valid_at: str | None = None
    max_valid_at: str | None = None
    forecasts: list[ForecastPoint] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            kind = row.get("kind", "")
            if not kind:
                continue
            summary["rows"] += 1
            if kind == "forecast":
                summary["forecast_rows"] += 1
            elif kind == "observation":
                summary["observation_rows"] += 1
            if row.get("dataset"):
                datasets[row["dataset"]] += 1
            if row.get("field"):
                fields[row["field"]] += 1
            row_time = row.get("time", "")
            min_time = _min_text(min_time, row_time)
            max_time = _max_text(max_time, row_time)
            if kind == "observation":
                observation_min_time = _min_text(observation_min_time, row_time)
                observation_max_time = _max_text(observation_max_time, row_time)
            valid_at = row.get("valid_at", "")
            min_valid_at = _min_text(min_valid_at, valid_at)
            max_valid_at = _max_text(max_valid_at, valid_at)
            if kind == "forecast" and valid_at:
                try:
                    forecasts.append(row_to_forecast(row))
                except ValueError:
                    continue

    summary.update(
        {
            "min_time": min_time,
            "max_time": max_time,
            "observation_min_time": observation_min_time,
            "observation_max_time": observation_max_time,
            "min_valid_at": min_valid_at,
            "max_valid_at": max_valid_at,
            "datasets": dict(datasets.most_common(12)),
            "fields": dict(fields.most_common(12)),
        }
    )
    _DWD_CACHE.clear()
    _DWD_CACHE[cache_key] = (summary, forecasts)
    payload = dict(summary)
    payload["deep"] = deep
    return payload, forecasts


def summarize_strunde_cache(path: Path) -> dict[str, Any]:
    rows = read_strunde_rows(path)
    times = [_parse_time(row.get("time", "")) for row in rows]
    valid_times = [item for item in times if item is not None]
    latest_time = max(valid_times) if valid_times else None
    values = [_float_or_none(row.get("value", "")) for row in rows]
    valid_values = [item for item in values if item is not None]
    latest_value = None
    if latest_time is not None:
        latest_rows = [row for row in rows if _parse_time(row.get("time", "")) == latest_time]
        latest_value = _float_or_none(latest_rows[-1].get("value", "")) if latest_rows else None
    return {
        "path": str(path),
        "exists": path.exists(),
        "rows": len(rows),
        "min_time": min(valid_times).isoformat() if valid_times else None,
        "max_time": latest_time.isoformat() if latest_time else None,
        "last_modified": _mtime(path),
        "latest_level_cm": latest_value,
        "min_level_cm": min(valid_values) if valid_values else None,
        "max_level_cm": max(valid_values) if valid_values else None,
        "stale": _is_stale(latest_time),
    }


def summarize_cached_comparison(settings: Settings, forecasts: list[ForecastPoint] | None = None) -> dict[str, Any]:
    if forecasts is None:
        return {
            "computed": False,
            "pairs": 0,
            "counts": {},
            "summary": {},
            "trainable": {variable: False for variable in sorted(MODEL_VARIABLES)},
            "min_points": TRAINING_MIN_POINTS,
        }
    try:
        forecasts = forecasts if forecasts is not None else ForecastCsvArchive(settings).read()
        model_fields = {LOCAL_FIELD_MAP[variable] for variable in MODEL_VARIABLES}
        observations = WeatherStationCsvCache(settings).observations_since(
            days=_observation_days_for_forecasts(settings, forecasts),
            fields=model_fields,
            measurements=None,
        )
        comparisons = match_forecasts_to_observations(forecasts, observations)
    except Exception as exc:  # noqa: BLE001 - GUI status should still render.
        return {
            "computed": True,
            "pairs": 0,
            "counts": {},
            "summary": {},
            "error": str(exc),
        }

    counts = Counter(item.variable for item in comparisons)
    return {
        "computed": True,
        "pairs": len(comparisons),
        "counts": dict(counts),
        "summary": summarize_comparisons(comparisons),
        "trainable": {
            variable: counts.get(variable, 0) >= TRAINING_MIN_POINTS
            for variable in sorted(MODEL_VARIABLES)
        },
        "min_points": TRAINING_MIN_POINTS,
    }


def _observation_days_for_forecasts(settings: Settings, forecasts: list[ForecastPoint]) -> int:
    if not forecasts:
        return 30
    oldest = min(item.valid_at for item in forecasts)
    age_days = ceil((datetime.now(timezone.utc) - oldest).total_seconds() / 86400) + 1
    return max(30, min(settings.local_cache_retention_days, age_days))


def summarize_models(model_dir: Path, comparison_counts: dict[str, int]) -> dict[str, Any]:
    model_files = sorted(model_dir.glob("*.joblib")) if model_dir.exists() else []
    latest_by_variable: dict[str, dict[str, Any]] = {}
    for variable in sorted(MODEL_VARIABLES):
        candidates = [
            path
            for path in model_files
            if path.name.startswith(f"{variable}-")
        ]
        if not candidates:
            continue
        latest = max(candidates, key=lambda path: path.stat().st_mtime)
        latest_by_variable[variable] = {
            "name": latest.name,
            "path": str(latest),
            "last_modified": _mtime(latest),
        }
    return {
        "path": str(model_dir),
        "exists": model_dir.exists(),
        "files": [
            {
                "name": path.name,
                "path": str(path),
                "last_modified": _mtime(path),
            }
            for path in model_files
        ],
        "latest_by_variable": latest_by_variable,
        "trainability": {
            variable: {
                "points": comparison_counts.get(variable, 0),
                "required": TRAINING_MIN_POINTS,
                "ready": comparison_counts.get(variable, 0) >= TRAINING_MIN_POINTS,
                "trained": variable in latest_by_variable,
                "latest_model": latest_by_variable.get(variable),
            }
            for variable in sorted(MODEL_VARIABLES)
        },
    }


def _safe_status_report(settings: Settings) -> StatusReport:
    try:
        return build_status(settings)
    except Exception as exc:  # noqa: BLE001
        report = StatusReport()
        report.warnings.append(f"Statuspruefung fehlgeschlagen: {exc}")
        return report


def _newest_local_time(report: StatusReport) -> datetime | None:
    return max((item.time for item in report.local_latest), default=None)


def _is_stale(latest: datetime | None) -> bool:
    if latest is None:
        return True
    return (datetime.now(timezone.utc) - latest).total_seconds() > 60 * 60 * 24


def _mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _float_or_none(value: str) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _min_datetime(current: datetime | None, candidate: datetime | None) -> datetime | None:
    if candidate is None:
        return current
    if current is None or candidate < current:
        return candidate
    return current


def _max_datetime(current: datetime | None, candidate: datetime | None) -> datetime | None:
    if candidate is None:
        return current
    if current is None or candidate > current:
        return candidate
    return current


def _min_text(current: str | None, candidate: str) -> str | None:
    if not candidate:
        return current
    if current is None or candidate < current:
        return candidate
    return current


def _max_text(current: str | None, candidate: str) -> str | None:
    if not candidate:
        return current
    if current is None or candidate > current:
        return candidate
    return current


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value
