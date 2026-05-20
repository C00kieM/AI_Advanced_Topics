from __future__ import annotations

from datetime import datetime, timezone

from .comparison import match_forecasts_to_observations, summarize_comparisons
from .config import Settings
from .dwd import DwdClient
from .influx import InfluxClient
from .ml import train_models
from .models import ForecastPoint, LocalObservation
from .mosmix import MosmixClient


class WeatherService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.influx = InfluxClient(settings)
        self.dwd = DwdClient(settings)
        self.mosmix = MosmixClient(settings)

    def latest_local(self) -> list[LocalObservation]:
        return self.influx.latest_observations()

    def current_forecast(self) -> list[ForecastPoint]:
        if self.settings.has_mosmix_station:
            return self.mosmix.fetch_forecasts()
        return self.dwd.fetch_forecasts()

    def ingest_dwd(self) -> dict[str, int | str]:
        forecasts = self.current_forecast()
        written = self.influx.write_forecasts(forecasts)
        return {"fetched": len(forecasts), "written": written, "at": datetime.now(timezone.utc).isoformat()}

    def latest_comparison_summary(self) -> dict[str, object]:
        forecasts = self.influx.archived_forecasts(since_days=30)
        observations = self.influx.local_rows_for_training(since_days=30)
        comparisons = match_forecasts_to_observations(forecasts, observations)
        return {
            "pairs": len(comparisons),
            "summary": summarize_comparisons(comparisons),
        }

    def train(self) -> dict[str, object]:
        forecasts = self.influx.archived_forecasts(since_days=30)
        observations = self.influx.local_rows_for_training(since_days=30)
        comparisons = match_forecasts_to_observations(forecasts, observations)
        result = train_models(comparisons, self.settings.model_dir)
        return {
            "trained": result.trained,
            "message": result.message,
            "metrics": result.metrics,
            "model_paths": result.model_paths,
        }
