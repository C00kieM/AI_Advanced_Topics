from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import csv
import io
import urllib.error
import urllib.parse
import urllib.request

from .config import Settings
from .models import ForecastPoint, LOCAL_FIELD_MAP, LocalObservation


class InfluxError(RuntimeError):
    pass


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _to_number(value: str) -> float | str:
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def escape_line_protocol(value: str) -> str:
    return value.replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


class InfluxClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def query_csv(self, flux: str, timeout: int = 15) -> list[dict[str, str]]:
        if not self.settings.has_influx_credentials:
            raise InfluxError("InfluxDB credentials are incomplete. Check .env.")
        url = (
            f"{self.settings.influx_url}/api/v2/query?"
            + urllib.parse.urlencode({"org": self.settings.influx_org})
        )
        request = urllib.request.Request(
            url=url,
            data=flux.encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Token {self.settings.influx_token}",
                "Accept": "application/csv",
                "Content-Type": "application/vnd.flux",
            },
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8-sig")
        except urllib.error.URLError as exc:
            raise InfluxError(f"InfluxDB query failed: {exc}") from exc
        if not payload.strip():
            return []
        reader = csv.DictReader(io.StringIO(payload))
        return [row for row in reader if row and row.get("_time") != "_time"]

    def latest_observations(self, measurement: str | None = None, days: int = 365) -> list[LocalObservation]:
        measurement = measurement or self.settings.local_measurement
        flux = latest_observations_flux(self.settings.influx_bucket, measurement, days)
        rows = self.query_csv(flux)
        observations: list[LocalObservation] = []
        for row in rows:
            field = row.get("_field", "")
            if not field:
                continue
            observations.append(
                LocalObservation(
                    measurement=row.get("_measurement", measurement),
                    field=field,
                    value=_to_number(row.get("_value", "")),
                    time=_parse_time(row["_time"]),
                )
            )
        return observations

    def latest_measurement_times(self, measurements: Iterable[str], field: str = "Lufttemperatur") -> dict[str, datetime | None]:
        result: dict[str, datetime | None] = {}
        for measurement in measurements:
            flux = latest_field_flux(self.settings.influx_bucket, measurement, field, 365)
            rows = self.query_csv(flux)
            result[measurement] = _parse_time(rows[0]["_time"]) if rows else None
        return result

    def write_forecasts(self, forecasts: Iterable[ForecastPoint], measurement: str = "dwd_forecast") -> int:
        lines = [forecast_to_line_protocol(item, measurement) for item in forecasts]
        payload = "\n".join(line for line in lines if line)
        if not payload:
            return 0
        url = (
            f"{self.settings.influx_url}/api/v2/write?"
            + urllib.parse.urlencode(
                {
                    "org": self.settings.influx_org,
                    "bucket": self.settings.influx_bucket,
                    "precision": "ns",
                }
            )
        )
        request = urllib.request.Request(
            url=url,
            data=payload.encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Token {self.settings.influx_token}",
                "Content-Type": "text/plain; charset=utf-8",
            },
        )
        try:
            with self._opener.open(request, timeout=15):
                return len(lines)
        except urllib.error.URLError as exc:
            raise InfluxError(f"InfluxDB write failed: {exc}") from exc

    def forecast_rows(self, since_days: int = 30) -> list[dict[str, str]]:
        flux = forecast_rows_flux(self.settings.influx_bucket, since_days)
        return self.query_csv(flux)

    def archived_forecasts(self, since_days: int = 30) -> list[ForecastPoint]:
        rows = self.forecast_rows(since_days)
        forecasts: list[ForecastPoint] = []
        for row in rows:
            try:
                value = float(row.get("_value", ""))
                valid_at = _parse_time(row["_time"])
                issued_at = _parse_time(row.get("issued_at", ""))
                horizon_hours = float(row.get("horizon_hours", "0"))
            except (KeyError, TypeError, ValueError):
                continue
            forecasts.append(
                ForecastPoint(
                    source=row.get("source", "dwd.api.bund.dev"),
                    station_id=row.get("station_id", ""),
                    variable=row.get("variable", row.get("_field", "")),
                    value=value,
                    issued_at=issued_at,
                    valid_at=valid_at,
                    horizon_hours=horizon_hours,
                    raw_name=row.get("raw_name", ""),
                )
            )
        return forecasts

    def local_rows_for_training(self, since_days: int = 30) -> list[LocalObservation]:
        flux = local_training_rows_flux(self.settings.influx_bucket, self.settings.local_measurement, since_days)
        rows = self.query_csv(flux)
        observations: list[LocalObservation] = []
        for row in rows:
            observations.append(
                LocalObservation(
                    measurement=row.get("_measurement", self.settings.local_measurement),
                    field=row.get("_field", ""),
                    value=_to_number(row.get("_value", "")),
                    time=_parse_time(row["_time"]),
                )
            )
        return observations

    def weather_station_rows_since(self, start: datetime) -> list[dict[str, str]]:
        flux = weather_station_rows_since_flux(self.settings.influx_bucket, start)
        return self.query_csv(flux, timeout=120)


def latest_observations_flux(bucket: str, measurement: str, days: int) -> str:
    return f'''
from(bucket: "{bucket}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r["_measurement"] == "{measurement}")
  |> group(columns: ["_field"])
  |> last()
  |> keep(columns: ["_measurement", "_time", "_field", "_value"])
'''.strip()


def latest_field_flux(bucket: str, measurement: str, field: str, days: int) -> str:
    return f'''
from(bucket: "{bucket}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r["_measurement"] == "{measurement}")
  |> filter(fn: (r) => r["_field"] == "{field}")
  |> last()
  |> keep(columns: ["_measurement", "_time", "_field", "_value"])
'''.strip()


def forecast_rows_flux(bucket: str, since_days: int) -> str:
    return f'''
from(bucket: "{bucket}")
  |> range(start: -{since_days}d)
  |> filter(fn: (r) => r["_measurement"] == "dwd_forecast")
  |> filter(fn: (r) => r["_field"] == "value")
  |> keep(columns: ["_time", "_field", "_value", "station_id", "variable", "issued_at", "horizon_hours", "source", "raw_name"])
'''.strip()


def local_training_rows_flux(bucket: str, measurement: str, since_days: int) -> str:
    fields = list(LOCAL_FIELD_MAP.values())
    filter_expr = " or ".join([f'r["_field"] == "{field}"' for field in fields])
    return f'''
from(bucket: "{bucket}")
  |> range(start: -{since_days}d)
  |> filter(fn: (r) => r["_measurement"] == "{measurement}")
  |> filter(fn: (r) => {filter_expr})
  |> keep(columns: ["_measurement", "_time", "_field", "_value"])
'''.strip()


def weather_station_rows_since_flux(bucket: str, start: datetime) -> str:
    start_iso = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return f'''
from(bucket: "{bucket}")
  |> range(start: time(v: "{start_iso}"))
  |> filter(fn: (r) => r["_measurement"] =~ /^wetterdaten-/)
  |> keep(columns: ["_time", "_measurement", "_field", "_value"])
'''.strip()


def forecast_to_line_protocol(forecast: ForecastPoint, measurement: str) -> str:
    tags = ",".join(
        [
            f"source={escape_line_protocol(forecast.source)}",
            f"station_id={escape_line_protocol(forecast.station_id or 'unknown')}",
            f"variable={escape_line_protocol(forecast.variable)}",
            f"issued_at={escape_line_protocol(forecast.issued_at.isoformat())}",
            f"horizon_hours={escape_line_protocol(f'{forecast.horizon_hours:.3f}')}",
            f"raw_name={escape_line_protocol(forecast.raw_name or forecast.variable)}",
        ]
    )
    timestamp = int(forecast.valid_at.timestamp() * 1_000_000_000)
    return (
        f"{escape_line_protocol(measurement)},{tags} "
        f"value={forecast.value} {timestamp}"
    )
