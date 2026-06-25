from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import csv

from .comparison import match_forecasts_to_observations, summarize_comparisons
from .config import Settings
from .diagnostics import build_status
from .forecast_archive import ForecastCsvArchive, read_forecast_rows, row_to_forecast
from .local_cache import WeatherStationCsvCache, read_cache_rows
from .models import ForecastPoint, MODEL_VARIABLES, StatusReport


TRAINING_MIN_POINTS = 24
_DWD_CACHE: dict[tuple[str, int, float], tuple[dict[str, Any], list[ForecastPoint]]] = {}


def build_gui_status(settings: Settings, live: bool = True, deep: bool = False) -> dict[str, Any]:
    report = _safe_status_report(settings) if live else StatusReport()
    local_cache = summarize_local_cache(settings.local_cache_path)
    dwd_data, forecasts = summarize_dwd_data(settings.dwd_data_path, deep=deep)
    comparison = summarize_cached_comparison(settings, forecasts)
    models = summarize_models(settings.model_dir, comparison["counts"])
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
        "dwd_data": dwd_data,
        "comparison": comparison,
        "models": models,
    }


def summarize_local_cache(path: Path) -> dict[str, Any]:
    rows = read_cache_rows(path)
    measurement_counts = Counter(row["measurement"] for row in rows)
    field_counts = Counter(row["field"] for row in rows)
    times = [_parse_time(row.get("time", "")) for row in rows]
    valid_times = [item for item in times if item is not None]
    return {
        "path": str(path),
        "exists": path.exists(),
        "rows": len(rows),
        "min_time": min(valid_times).isoformat() if valid_times else None,
        "max_time": max(valid_times).isoformat() if valid_times else None,
        "last_modified": _mtime(path),
        "measurements": dict(measurement_counts.most_common()),
        "fields": dict(field_counts.most_common()),
        "stale": _is_stale(max(valid_times) if valid_times else None),
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
        "min_valid_at": None,
        "max_valid_at": None,
        "datasets": {},
        "fields": {},
        "last_modified": _mtime(path),
    }
    if not path.exists():
        return summary, []
    if not deep:
        forecasts = [row_to_forecast(row) for row in read_forecast_rows(path)]
        valid_times = [item.valid_at.isoformat() for item in forecasts]
        summary.update(
            {
                "rows": None,
                "observation_rows": None,
                "forecast_rows": len(forecasts),
                "min_valid_at": min(valid_times) if valid_times else None,
                "max_valid_at": max(valid_times) if valid_times else None,
            }
        )
        return summary, forecasts

    cache_key = (str(path.resolve()), path.stat().st_size, path.stat().st_mtime)
    cached = _DWD_CACHE.get(cache_key)
    if cached:
        return cached

    datasets: Counter[str] = Counter()
    fields: Counter[str] = Counter()
    min_time: str | None = None
    max_time: str | None = None
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
            "min_valid_at": min_valid_at,
            "max_valid_at": max_valid_at,
            "datasets": dict(datasets.most_common(12)),
            "fields": dict(fields.most_common(12)),
        }
    )
    _DWD_CACHE.clear()
    _DWD_CACHE[cache_key] = (summary, forecasts)
    return summary, forecasts


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
        observations = WeatherStationCsvCache(settings).observations_since(days=30)
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


def summarize_models(model_dir: Path, comparison_counts: dict[str, int]) -> dict[str, Any]:
    model_files = sorted(model_dir.glob("*.joblib")) if model_dir.exists() else []
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
        "trainability": {
            variable: {
                "points": comparison_counts.get(variable, 0),
                "required": TRAINING_MIN_POINTS,
                "ready": comparison_counts.get(variable, 0) >= TRAINING_MIN_POINTS,
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
