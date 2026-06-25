from __future__ import annotations

from datetime import datetime, timezone
from math import ceil

from .comparison import match_forecasts_to_observations, summarize_comparisons
from .config import Settings
from .dwd import DwdClient
from .forecast_archive import ForecastCsvArchive
from .influx import InfluxClient, InfluxError
from .local_cache import WeatherStationCsvCache
from .ml import train_models
from .models import ForecastPoint, LOCAL_FIELD_MAP, LocalObservation, MODEL_VARIABLES
from .mosmix import MosmixClient


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
        return {
            "fetched": result.fetched,
            "written_rows": result.written_rows,
            "path": str(result.path),
            "at": result.at.isoformat(),
        }

    def latest_comparison_summary(self) -> dict[str, object]:
        comparisons = self._comparison_points()
        return {
            "pairs": len(comparisons),
            "summary": summarize_comparisons(comparisons),
        }

    def train(self) -> dict[str, object]:
        comparisons = self._comparison_points()
        result = train_models(comparisons, self.settings.model_dir)
        return {
            "trained": result.trained,
            "message": result.message,
            "metrics": result.metrics,
            "model_paths": result.model_paths,
        }

    def _comparison_points(self) -> list:
        forecasts = self.forecast_archive.read()
        observations = self._local_training_rows(since_days=self._observation_days_for_forecasts(forecasts))
        return match_forecasts_to_observations(forecasts, observations)

    def _observation_days_for_forecasts(self, forecasts: list[ForecastPoint]) -> int:
        if not forecasts:
            return 30
        oldest = min(item.valid_at for item in forecasts)
        age_days = ceil((datetime.now(timezone.utc) - oldest).total_seconds() / 86400) + 1
        return max(30, min(self.settings.local_cache_retention_days, age_days))

    def _local_training_rows(self, since_days: int) -> list[LocalObservation]:
        model_fields = {LOCAL_FIELD_MAP[variable] for variable in MODEL_VARIABLES}
        cached = self.local_cache.observations_since(days=since_days, fields=model_fields)
        if cached:
            return cached
        try:
            return self.influx.local_rows_for_training(since_days=since_days)
        except InfluxError:
            return []
