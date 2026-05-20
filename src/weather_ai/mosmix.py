from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import math
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

from .config import Settings
from .models import ForecastPoint


class MosmixError(RuntimeError):
    pass


MOSMIX_VARIABLE_MAP = {
    "TTT": ("temperature", "K"),
    "Td": ("dew_point", "K"),
    "DD": ("wind_direction", "degree"),
    "FF": ("wind_speed", "m/s"),
    "FX1": ("wind_gust_1h", "m/s"),
    "FX3": ("wind_gust_3h", "m/s"),
    "RR1c": ("precipitation", "kg/m2"),
    "RR3c": ("precipitation_3h", "kg/m2"),
    "Neff": ("cloud_cover_effective", "%"),
    "PPPP": ("pressure", "Pa"),
    "Rad1h": ("radiation_global", "kJ/m2"),
    "SunD1": ("sunshine_duration", "s"),
}

KML_NS = "http://www.opengis.net/kml/2.2"
DWD_NS = "https://opendata.dwd.de/weather/lib/pointforecast_dwd_extension_V1_0.xsd"
NS = {"kml": KML_NS, "dwd": DWD_NS}


class MosmixClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def fetch_forecasts(self) -> list[ForecastPoint]:
        if not self.settings.has_mosmix_station:
            raise MosmixError("MOSMIX_STATION_ID is missing. Set it in .env.")
        payload = self.fetch_kmz()
        return parse_mosmix_kmz(payload, self.settings.mosmix_station_id)

    def fetch_kmz(self) -> bytes:
        url = mosmix_latest_url(
            base_url=self.settings.mosmix_base_url,
            station_id=self.settings.mosmix_station_id,
            product=self.settings.mosmix_product,
        )
        request = urllib.request.Request(url=url, method="GET", headers={"Accept": "application/octet-stream"})
        try:
            with self._opener.open(request, timeout=30) as response:
                return response.read()
        except urllib.error.URLError as exc:
            raise MosmixError(f"MOSMIX request failed: {exc}") from exc


def mosmix_latest_url(base_url: str, station_id: str, product: str = "MOSMIX_L") -> str:
    normalized_product = product.upper()
    if normalized_product not in {"MOSMIX_L", "MOSMIX_S"}:
        raise MosmixError("MOSMIX_PRODUCT must be MOSMIX_L or MOSMIX_S.")
    suffix = "LATEST" if normalized_product == "MOSMIX_L" else "LATEST_240"
    return (
        f"{base_url.rstrip('/')}/weather/local_forecasts/mos/{normalized_product}/"
        f"single_stations/{station_id}/kml/{normalized_product}_{suffix}_{station_id}.kmz"
    )


def parse_mosmix_kmz(payload: bytes, station_id: str) -> list[ForecastPoint]:
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            kml_name = _find_kml_name(archive)
            return parse_mosmix_kml(archive.read(kml_name), station_id)
    except zipfile.BadZipFile as exc:
        raise MosmixError("MOSMIX payload is not a valid KMZ/ZIP file.") from exc


def parse_mosmix_kml(payload: bytes | str, station_id: str) -> list[ForecastPoint]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise MosmixError(f"MOSMIX KML could not be parsed: {exc}") from exc

    issued_at = _issue_time(root)
    time_steps = _time_steps(root)
    if not time_steps:
        raise MosmixError("MOSMIX KML does not contain ForecastTimeSteps.")
    placemark = _station_placemark(root, station_id)
    if placemark is None:
        raise MosmixError(f"MOSMIX station {station_id} not found in KML.")

    points: list[ForecastPoint] = []
    for forecast in placemark.findall(".//dwd:Forecast", NS):
        raw_name = forecast.attrib.get(f"{{{DWD_NS}}}elementName") or forecast.attrib.get("elementName")
        if raw_name not in MOSMIX_VARIABLE_MAP:
            continue
        variable, unit = MOSMIX_VARIABLE_MAP[raw_name]
        values = _forecast_values(forecast)
        for valid_at, raw_value in zip(time_steps, values, strict=False):
            if raw_value is None or math.isnan(raw_value):
                continue
            points.append(
                ForecastPoint(
                    source="dwd-opendata-mosmix",
                    station_id=station_id,
                    variable=variable,
                    value=_normalize_mosmix_value(variable, raw_value),
                    issued_at=issued_at,
                    valid_at=valid_at,
                    horizon_hours=(valid_at - issued_at).total_seconds() / 3600,
                    unit=_normalized_unit(variable, unit),
                    raw_name=raw_name,
                )
            )
    return points


def _find_kml_name(archive: zipfile.ZipFile) -> str:
    for name in archive.namelist():
        if name.lower().endswith(".kml"):
            return name
    raise MosmixError("MOSMIX KMZ does not contain a KML file.")


def _issue_time(root: ET.Element) -> datetime:
    issue = root.find(".//dwd:IssueTime", NS)
    if issue is None or not issue.text:
        return datetime.now(timezone.utc)
    return _parse_iso_time(issue.text)


def _time_steps(root: ET.Element) -> list[datetime]:
    return [_parse_iso_time(item.text or "") for item in root.findall(".//dwd:ForecastTimeSteps/dwd:TimeStep", NS)]


def _station_placemark(root: ET.Element, station_id: str) -> ET.Element | None:
    for placemark in root.findall(".//kml:Placemark", NS):
        name = placemark.find("kml:name", NS)
        if name is not None and (name.text or "").strip() == station_id:
            return placemark
    return None


def _forecast_values(forecast: ET.Element) -> list[float | None]:
    value_node = forecast.find("dwd:value", NS)
    if value_node is None or not value_node.text:
        return []
    values: list[float | None] = []
    for raw_value in value_node.text.split():
        if raw_value == "-":
            values.append(None)
            continue
        try:
            values.append(float(raw_value))
        except ValueError:
            values.append(None)
    return values


def _parse_iso_time(value: str) -> datetime:
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).astimezone(timezone.utc)


def _normalize_mosmix_value(variable: str, value: float) -> float:
    if variable in {"temperature", "dew_point"}:
        return value - 273.15
    if variable == "pressure" and value > 2000:
        return value / 100
    return value


def _normalized_unit(variable: str, unit: str) -> str:
    if variable in {"temperature", "dew_point"}:
        return "degC"
    if variable == "pressure":
        return "hPa"
    if variable in {"precipitation", "precipitation_3h"}:
        return "mm"
    return unit
