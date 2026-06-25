from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import json
import math
import urllib.error
import urllib.parse
import urllib.request

from .config import Settings
from .models import ForecastPoint


class DwdError(RuntimeError):
    pass


DWD_VARIABLE_MAP = {
    "temperature": ("temperature", "TTT", "temp"),
    "precipitation": ("precipitationTotal", "precipitation", "RR1c", "rain"),
    "wind_speed": ("windSpeed", "wind_speed", "FF", "wind"),
    "wind_direction": ("windDirection", "wind_direction", "DD"),
    "pressure": ("pressure", "PPPP"),
    "humidity": ("humidity", "humidityRelative", "UU"),
}


class DwdClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def fetch_station_overview(self, timeout: int = 15) -> dict[str, Any]:
        if not self.settings.has_dwd_station:
            raise DwdError("DWD_STATION_ID is missing. Set it in .env.")
        url = (
            f"{self.settings.dwd_base_url}/stationOverviewExtended?"
            + urllib.parse.urlencode({"stationIds": self.settings.dwd_station_id})
        )
        request = urllib.request.Request(url=url, method="GET", headers={"Accept": "application/json"})
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise DwdError(f"DWD request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise DwdError(f"DWD response was not JSON: {exc}") from exc

    def fetch_forecasts(self, timeout: int = 15) -> list[ForecastPoint]:
        payload = self.fetch_station_overview(timeout=timeout)
        return parse_station_overview(payload, self.settings.dwd_station_id)


def parse_station_overview(payload: dict[str, Any], station_id: str) -> list[ForecastPoint]:
    station = _station_payload(payload, station_id)
    issued_at = _issued_at(station)
    points: list[ForecastPoint] = []
    for forecast_block in _forecast_blocks(station):
        start = _forecast_start(forecast_block, issued_at)
        step = _forecast_step(forecast_block)
        for variable, candidate_names in DWD_VARIABLE_MAP.items():
            raw_name, values = _first_series(forecast_block, candidate_names)
            if not values:
                continue
            for index, raw_value in enumerate(values):
                value = _coerce_float(raw_value)
                if value is None or math.isnan(value):
                    continue
                valid_at = start + step * index
                value = _normalize_value(variable, value)
                points.append(
                    ForecastPoint(
                        source="dwd.api.bund.dev",
                        station_id=station_id,
                        variable=variable,
                        value=value,
                        issued_at=issued_at,
                        valid_at=valid_at,
                        horizon_hours=(valid_at - issued_at).total_seconds() / 3600,
                        raw_name=raw_name,
                    )
                )
    return points


def _station_payload(payload: dict[str, Any], station_id: str) -> dict[str, Any]:
    if station_id and isinstance(payload.get(station_id), dict):
        return payload[station_id]
    stations = payload.get("stations")
    if isinstance(stations, dict) and station_id in stations:
        return stations[station_id]
    if isinstance(stations, list) and stations:
        for item in stations:
            if str(item.get("stationId") or item.get("id")) == station_id:
                return item
        return stations[0]
    for value in payload.values():
        if isinstance(value, dict):
            return value
    return payload


def _forecast_blocks(station: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for key, value in station.items():
        if key.lower().startswith("forecast") and isinstance(value, dict):
            blocks.append(value)
    if not blocks and isinstance(station.get("forecast"), dict):
        blocks.append(station["forecast"])
    return blocks


def _issued_at(station: dict[str, Any]) -> datetime:
    for key in ("forecastStart", "forecastIssued", "lastUpdate", "updateTime"):
        value = station.get(key)
        parsed = _parse_epoch_or_iso(value)
        if parsed:
            return parsed
    return datetime.now(timezone.utc)


def _forecast_start(block: dict[str, Any], default: datetime) -> datetime:
    for key in ("start", "forecastStart", "timeStart"):
        parsed = _parse_epoch_or_iso(block.get(key))
        if parsed:
            return parsed
    return default


def _forecast_step(block: dict[str, Any]) -> timedelta:
    raw = block.get("timeStep") or block.get("step") or 3600
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 3600
    if value > 10_000:
        value = value / 1000
    return timedelta(seconds=value)


def _parse_epoch_or_iso(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _first_series(block: dict[str, Any], names: tuple[str, ...]) -> tuple[str, list[Any]]:
    for name in names:
        value = block.get(name)
        if isinstance(value, list):
            return name, value
    return "", []


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_value(variable: str, value: float) -> float:
    if variable == "temperature" and abs(value) > 80:
        return value / 10
    return value
