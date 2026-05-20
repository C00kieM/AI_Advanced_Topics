from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


LOCAL_FIELD_MAP = {
    "temperature": "Lufttemperatur",
    "humidity": "Luftfeuchtigkeit",
    "pressure": "Luftdruck",
    "precipitation": "Niederschlag",
    "wind_speed": "wind_speed",
    "wind_direction": "wind_direction",
    "light": "LichtIntensitaet",
    "uv_index": "UVIndex",
}

MODEL_VARIABLES = {"temperature", "precipitation", "wind_speed"}


@dataclass(frozen=True)
class LocalObservation:
    measurement: str
    field: str
    value: float | str
    time: datetime

    @property
    def variable(self) -> str:
        for variable, field_name in LOCAL_FIELD_MAP.items():
            if field_name == self.field:
                return variable
        return self.field


@dataclass(frozen=True)
class ForecastPoint:
    source: str
    station_id: str
    variable: str
    value: float
    issued_at: datetime
    valid_at: datetime
    horizon_hours: float
    unit: str = ""
    raw_name: str = ""


@dataclass(frozen=True)
class ComparisonPoint:
    variable: str
    forecast_value: float
    actual_value: float
    error: float
    forecast_valid_at: datetime
    observation_time: datetime
    horizon_hours: float


@dataclass
class StatusReport:
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    influx_ok: bool = False
    dwd_ok: bool = False
    local_latest: list[LocalObservation] = field(default_factory=list)
    candidate_measurements: dict[str, datetime | None] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_local_stale(self) -> bool:
        if not self.local_latest:
            return True
        newest = max(item.time for item in self.local_latest)
        age = self.generated_at - newest
        return age.total_seconds() > 60 * 60 * 24
