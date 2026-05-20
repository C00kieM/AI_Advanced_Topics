from __future__ import annotations

from collections import defaultdict

from .config import Settings
from .diagnostics import build_status
from .dwd import DwdClient
from .models import ForecastPoint, LocalObservation
from .mosmix import MosmixClient


VARIABLE_LABELS = {
    "temperature": "Temperatur",
    "precipitation": "Niederschlag",
    "wind_speed": "Wind",
    "wind_direction": "Windrichtung",
    "pressure": "Luftdruck",
    "humidity": "Luftfeuchtigkeit",
}


class ChatService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def answer(self, question: str) -> str:
        status = build_status(self.settings)
        forecasts, forecast_error = self._fetch_forecasts()
        lines = [
            "Kurzantwort:",
            self._headline(status, forecasts, forecast_error),
            "",
            "Datenlage:",
            *_status_lines(status),
            "",
            "DWD-Prognose:",
            *_forecast_lines(forecasts, forecast_error),
            "",
            "Lokale Einschaetzung:",
            *_local_interpretation(status, forecasts),
            "",
            f"Frage: {question}",
        ]
        return "\n".join(lines)

    def _fetch_forecasts(self) -> tuple[list[ForecastPoint], str | None]:
        if self.settings.has_mosmix_station:
            try:
                forecasts = MosmixClient(self.settings).fetch_forecasts()
            except Exception as exc:  # noqa: BLE001 - chat should degrade gracefully.
                return [], f"MOSMIX-Prognose konnte nicht abgerufen werden: {exc}"
            if not forecasts:
                return [], "MOSMIX lieferte keine parsebaren Forecast-Punkte."
            return forecasts, None
        if not self.settings.has_dwd_station:
            return [], "Keine MOSMIX_STATION_ID oder DWD_STATION_ID konfiguriert."
        try:
            forecasts = DwdClient(self.settings).fetch_forecasts()
        except Exception as exc:  # noqa: BLE001 - chat should degrade gracefully.
            return [], f"DWD-Prognose konnte nicht abgerufen werden: {exc}"
        if not forecasts:
            return [], "DWD lieferte keine parsebaren Forecast-Punkte."
        return forecasts, None

    def _headline(self, status, forecasts: list[ForecastPoint], forecast_error: str | None) -> str:
        if status.is_local_stale and forecast_error:
            return "Aktuell ist keine belastbare lokale Wetterantwort moeglich: lokale Daten sind veraltet und DWD ist nicht voll konfiguriert."
        if status.is_local_stale:
            return "Die DWD-Prognose kann angezeigt werden, aber die lokale Korrektur ist wegen veralteter Messdaten nicht belastbar."
        if forecast_error:
            return "Lokale Messdaten sind vorhanden, aber die DWD-Prognose fehlt noch fuer den Vergleich."
        if forecasts:
            return "DWD-Prognose und lokale Datenbasis sind verfuegbar; die lokale Korrektur haengt von gesammelten Forecast-vs-Ist-Paaren ab."
        return "Es liegen noch nicht genug Daten fuer eine belastbare Antwort vor."


def _status_lines(status) -> list[str]:
    lines = [f"- InfluxDB: {'OK' if status.influx_ok else 'FEHLER'}"]
    latest = max((item.time for item in status.local_latest), default=None)
    if latest:
        lines.append(f"- Neuester lokaler Messwert: {latest.isoformat()}")
    else:
        lines.append("- Neuester lokaler Messwert: keine Daten")
    if status.is_local_stale:
        lines.append("- Bewertung: lokale Daten sind veraltet; lokale Erfahrung nur eingeschraenkt nutzbar.")
    if status.warnings:
        lines.extend([f"- Hinweis: {warning}" for warning in status.warnings])
    return lines


def _forecast_lines(forecasts: list[ForecastPoint], forecast_error: str | None) -> list[str]:
    if forecast_error:
        return [f"- {forecast_error}"]
    if not forecasts:
        return ["- Keine Forecast-Punkte vorhanden."]
    grouped: dict[str, list[ForecastPoint]] = defaultdict(list)
    for item in forecasts:
        if item.variable in {"temperature", "precipitation", "wind_speed"}:
            grouped[item.variable].append(item)
    lines: list[str] = []
    for variable in ("temperature", "precipitation", "wind_speed"):
        items = sorted(grouped.get(variable, []), key=lambda item: item.valid_at)[:3]
        if not items:
            continue
        label = VARIABLE_LABELS.get(variable, variable)
        values = ", ".join([f"{item.value:g} fuer {item.valid_at:%d.%m. %H:%M} UTC" for item in items])
        lines.append(f"- {label}: {values}")
    return lines or ["- DWD lieferte keine der priorisierten Variablen Temperatur, Niederschlag oder Wind."]


def _local_interpretation(status, forecasts: list[ForecastPoint]) -> list[str]:
    latest_by_variable = _latest_observations_by_variable(status.local_latest)
    lines: list[str] = []
    temperature = latest_by_variable.get("temperature")
    wind = latest_by_variable.get("wind_speed")
    precipitation = latest_by_variable.get("precipitation")
    if temperature:
        lines.append(f"- Letzte lokale Temperatur: {_format_value(temperature.value)} Grad.")
    if precipitation:
        lines.append(f"- Letzter lokaler Niederschlag: {_format_value(precipitation.value)}.")
    if wind:
        lines.append(f"- Letzte lokale Windgeschwindigkeit: {_format_value(wind.value)}.")
    if status.is_local_stale:
        lines.append("- Keine automatische lokale Korrektur anwenden, bis wieder aktuelle Stationsdaten ankommen.")
    elif forecasts:
        lines.append("- Lokale Korrektur wird belastbarer, sobald archivierte DWD-Prognosen mit spaeteren Ist-Werten ueberlappen.")
    if not lines:
        lines.append("- Keine lokalen Kernmesswerte fuer Temperatur, Niederschlag oder Wind gefunden.")
    return lines


def _latest_observations_by_variable(observations: list[LocalObservation]) -> dict[str, LocalObservation]:
    latest: dict[str, LocalObservation] = {}
    for observation in observations:
        current = latest.get(observation.variable)
        if current is None or observation.time > current.time:
            latest[observation.variable] = observation
    return latest


def _format_value(value: float | str) -> str:
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)
