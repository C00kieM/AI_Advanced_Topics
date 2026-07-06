from __future__ import annotations

from datetime import datetime, timezone
import csv
import io
import urllib.error
import urllib.parse
import urllib.request

from .config import Settings
from .models import LOCAL_FIELD_MAP, LocalObservation


class InfluxError(RuntimeError):
    pass


class InfluxWriteBlockedError(InfluxError):
    pass


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _to_number(value: str) -> float | str:
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


class InfluxClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _open(self, request: urllib.request.Request, timeout: int):
        if "/api/v2/write" in request.full_url:
            raise InfluxWriteBlockedError("InfluxDB writes are blacklisted. This app may only read from InfluxDB.")
        return self._opener.open(request, timeout=timeout)

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
            with self._open(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8-sig")
        except urllib.error.URLError as exc:
            raise InfluxError(f"InfluxDB query failed: {exc}") from exc
        if not payload.strip():
            return []
        reader = csv.DictReader(io.StringIO(payload))
        return [row for row in reader if row and row.get("_time") != "_time"]

    def latest_observations(self, measurement: str | None = None, days: int = 365, timeout: int = 15) -> list[LocalObservation]:
        measurement = measurement or self.settings.local_measurement
        flux = latest_observations_flux(self.settings.influx_bucket, measurement, days)
        rows = self.query_csv(flux, timeout=timeout)
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

    def latest_measurement_times(self, measurements: Iterable[str], field: str = "Lufttemperatur", timeout: int = 15) -> dict[str, datetime | None]:
        result: dict[str, datetime | None] = {}
        for measurement in measurements:
            flux = latest_field_flux(self.settings.influx_bucket, measurement, field, 365)
            rows = self.query_csv(flux, timeout=timeout)
            result[measurement] = _parse_time(rows[0]["_time"]) if rows else None
        return result

    def write_forecasts(self, *args, **kwargs) -> int:  # noqa: ARG002
        raise InfluxWriteBlockedError("InfluxDB writes are blacklisted. This app may only read from InfluxDB.")

    def forecast_rows(self, since_days: int = 30) -> list[dict[str, str]]:
        raise InfluxWriteBlockedError("DWD forecast archives must be read from local CSV, not InfluxDB.")

    def archived_forecasts(self, since_days: int = 30) -> list[object]:  # noqa: ARG002
        raise InfluxWriteBlockedError("DWD forecast archives must be read from local CSV, not InfluxDB.")

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

    def strunde_level_rows_since(self, start: datetime) -> list[dict[str, str]]:
        flux = strunde_level_rows_since_flux(
            self.settings.influx_bucket,
            self.settings.strunde_measurement,
            self.settings.strunde_level_field,
            start,
        )
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


def strunde_level_rows_since_flux(bucket: str, measurement: str, field: str, start: datetime) -> str:
    start_iso = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    bucket_value = _flux_string(bucket)
    measurement_value = _flux_string(measurement)
    field_value = _flux_string(field)
    return f'''
from(bucket: "{bucket_value}")
  |> range(start: time(v: "{start_iso}"))
  |> filter(fn: (r) => r["_measurement"] == "{measurement_value}")
  |> filter(fn: (r) => r["_field"] == "{field_value}")
  |> keep(columns: ["_time", "_measurement", "_field", "_value"])
'''.strip()


def _flux_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
