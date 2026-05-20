from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _float_env(name: str) -> float | None:
    value = os.getenv(name)
    if value in (None, ""):
        return None
    return float(value)


@dataclass(frozen=True)
class Settings:
    influx_url: str
    influx_token: str
    influx_org: str
    influx_bucket: str
    local_measurement: str
    local_lat: float | None
    local_lon: float | None
    dwd_station_id: str
    dwd_base_url: str
    mosmix_station_id: str
    mosmix_product: str
    mosmix_base_url: str
    model_dir: Path
    local_cache_path: Path
    local_cache_retention_days: int
    local_cache_sync_on_startup: bool
    dwd_cdc_base_url: str
    dwd_historical_station_ids: list[str]
    dwd_historical_resolution: str
    dwd_historical_parameters: list[str]
    dwd_historical_retention_days: int
    dwd_historical_cache_path: Path
    forecast_archive_path: Path

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        return cls(
            influx_url=os.getenv("INFLUX_URL", "http://192.168.31.217:8086").rstrip("/"),
            influx_token=os.getenv("INFLUX_TOKEN", ""),
            influx_org=os.getenv("INFLUX_ORG", "63a16fe229625e74"),
            influx_bucket=os.getenv("INFLUX_BUCKET", "iot"),
            local_measurement=os.getenv("LOCAL_MEASUREMENT", "wetterdaten-gl-fw-1"),
            local_lat=_float_env("LOCAL_LAT"),
            local_lon=_float_env("LOCAL_LON"),
            dwd_station_id=os.getenv("DWD_STATION_ID", ""),
            dwd_base_url=os.getenv("DWD_BASE_URL", "https://dwd.api.proxy.bund.dev/v30").rstrip("/"),
            mosmix_station_id=os.getenv("MOSMIX_STATION_ID", os.getenv("DWD_STATION_ID", "")),
            mosmix_product=os.getenv("MOSMIX_PRODUCT", "MOSMIX_L"),
            mosmix_base_url=os.getenv("MOSMIX_BASE_URL", "http://opendata.dwd.de").rstrip("/"),
            model_dir=Path(os.getenv("MODEL_DIR", ".weather-ai-models")),
            local_cache_path=Path(os.getenv("LOCAL_CACHE_PATH", "data/local_weather_history.csv")),
            local_cache_retention_days=int(os.getenv("LOCAL_CACHE_RETENTION_DAYS", "1095")),
            local_cache_sync_on_startup=os.getenv("LOCAL_CACHE_SYNC_ON_STARTUP", "true").lower()
            in {"1", "true", "yes", "on"},
            dwd_cdc_base_url=os.getenv("DWD_CDC_BASE_URL", "http://opendata.dwd.de/climate_environment/CDC").rstrip("/"),
            dwd_historical_station_ids=[
                item.strip().zfill(5)
                for item in os.getenv("DWD_HISTORICAL_STATION_IDS", "02667").split(",")
                if item.strip()
            ],
            dwd_historical_resolution=os.getenv("DWD_HISTORICAL_RESOLUTION", "10_minutes"),
            dwd_historical_parameters=[
                item.strip()
                for item in os.getenv("DWD_HISTORICAL_PARAMETERS", "air_temperature,precipitation,wind").split(",")
                if item.strip()
            ],
            dwd_historical_retention_days=int(os.getenv("DWD_HISTORICAL_RETENTION_DAYS", "1095")),
            dwd_historical_cache_path=Path(os.getenv("DWD_HISTORICAL_CACHE_PATH", "data/dwd_historical_weather.csv")),
            forecast_archive_path=Path(os.getenv("FORECAST_ARCHIVE_PATH", "data/dwd_forecast_archive.csv")),
        )

    @property
    def has_influx_credentials(self) -> bool:
        return bool(self.influx_url and self.influx_token and self.influx_org and self.influx_bucket)

    @property
    def has_dwd_station(self) -> bool:
        return bool(self.dwd_station_id)

    @property
    def has_mosmix_station(self) -> bool:
        return bool(self.mosmix_station_id)
