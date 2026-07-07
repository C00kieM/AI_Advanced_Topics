from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any
import json

from .comparison import match_forecasts_to_observations, summarize_comparisons
from .config import Settings
from .daily_profile import append_profiles, corrected_forecasts, daily_profile_path, profiles_for_forecasts
from .dwd import DwdClient
from .forecast_archive import ForecastCsvArchive
from .influx import InfluxClient
from .local_cache import WeatherStationCsvCache
from .ml import train_models
from .models import ForecastPoint, LOCAL_FIELD_MAP, LocalObservation, MODEL_VARIABLES
from .mosmix import MosmixClient
from .stations import StationScope, scoped_model_dir


class WeatherService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.influx = InfluxClient(settings)
        self.dwd = DwdClient(settings)
        self.mosmix = MosmixClient(settings)
        self.forecast_archive = ForecastCsvArchive(settings)
        self.local_cache = WeatherStationCsvCache(settings)

    def latest_local(self) -> list[LocalObservation]:
        return self.influx.latest_observations()

    def current_forecast(self) -> list[ForecastPoint]:
        if self.settings.has_mosmix_station:
            return self.mosmix.fetch_forecasts()
        return self.dwd.fetch_forecasts()

    def archive_dwd_forecast(self) -> dict[str, int | str]:
        forecasts = self.current_forecast()
        result = self.forecast_archive.append(forecasts)
        profiles_written = self._store_daily_profiles(forecasts)
        return {
            "fetched": result.fetched,
            "written_rows": result.written_rows,
            "profiles_written": profiles_written,
            "path": str(result.path),
            "at": result.at.isoformat(),
        }

    def latest_comparison_summary(self, station_scope: StationScope | None = None) -> dict[str, object]:
        comparisons = self._comparison_points(station_scope)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": _scope_payload(station_scope),
            "pairs": len(comparisons),
            "summary": summarize_comparisons(comparisons),
        }
        path = _write_json(scoped_model_dir(self.settings, station_scope), "comparison_summary.json", payload)
        return {**payload, "saved_to": str(path)}

    def train(self, station_scope: StationScope | None = None) -> dict[str, object]:
        model_dir = scoped_model_dir(self.settings, station_scope)
        comparisons = self._comparison_points(station_scope)
        result = train_models(comparisons, model_dir)
        profiles_written = 0
        if result.trained:
            profiles_written = self._store_daily_profiles(self.forecast_archive.read(), model_dir=model_dir)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "trained": result.trained,
            "message": result.message,
            "metrics": result.metrics,
            "model_paths": result.model_paths,
            "profiles_written": profiles_written,
            "scope": _scope_payload(station_scope),
        }
        path = _write_json(model_dir, "training_metrics.json", payload)
        return {**payload, "metrics_saved_to": str(path)}

    def _store_daily_profiles(self, forecasts: list[ForecastPoint], model_dir=None) -> int:
        model_dir = model_dir or scoped_model_dir(self.settings)
        dwd_profiles = profiles_for_forecasts(forecasts, source="dwd")
        local_profiles = profiles_for_forecasts(
            corrected_forecasts(forecasts, model_dir),
            source="local-corrected",
        )
        return append_profiles(daily_profile_path(self.settings), [*dwd_profiles, *local_profiles])

    def _comparison_points(self, station_scope: StationScope | None = None) -> list:
        forecasts = self.forecast_archive.read()
        if not forecasts:
            return []
        observations = self._local_training_rows(
            since_days=self._observation_days_for_forecasts(forecasts),
            station_scope=station_scope,
        )
        return match_forecasts_to_observations(forecasts, observations)

    def _observation_days_for_forecasts(self, forecasts: list[ForecastPoint]) -> int:
        if not forecasts:
            return 30
        oldest = min(item.valid_at for item in forecasts)
        age_days = ceil((datetime.now(timezone.utc) - oldest).total_seconds() / 86400) + 1
        return max(30, min(self.settings.local_cache_retention_days, age_days))

    def _local_training_rows(self, since_days: int, station_scope: StationScope | None = None) -> list[LocalObservation]:
        model_fields = {LOCAL_FIELD_MAP[variable] for variable in MODEL_VARIABLES}
        return self.local_cache.observations_since(
            days=since_days,
            fields=model_fields,
            measurements=station_scope.measurements if station_scope else None,
        )


def _scope_payload(station_scope: StationScope | None) -> dict[str, object]:
    if station_scope is None:
        return {"kind": "all", "label": "alle Stationen", "measurements": None}
    return {
        "kind": station_scope.kind,
        "label": station_scope.label,
        "measurements": sorted(station_scope.measurements) if station_scope.measurements else None,
    }


def _write_json(directory: Path, filename: str, payload: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path
