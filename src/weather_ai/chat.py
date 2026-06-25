from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv
import re

from .config import Settings
from .daily_profile import (
    DailyProfile,
    append_profiles,
    corrected_forecasts,
    daily_profile_path,
    latest_profiles_for_date,
    profiles_for_forecasts,
)
from .diagnostics import build_status
from .dwd import DwdClient
from .forecast_archive import ForecastCsvArchive
from .local_cache import WeatherStationCsvCache
from .models import ForecastPoint, LOCAL_FIELD_MAP, LocalObservation
from .mosmix import MosmixClient


VARIABLE_LABELS = {
    "temperature": "Temperatur",
    "precipitation": "Niederschlag",
    "wind_speed": "Wind",
    "wind_direction": "Windrichtung",
    "pressure": "Luftdruck",
    "humidity": "Luftfeuchtigkeit",
}

MONTHS_DE = {
    "januar": 1,
    "februar": 2,
    "maerz": 3,
    "marz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

DWD_HISTORY_FIELD_MAP = {
    "TT_10": "temperature",
    "RWS_10": "precipitation",
    "FF_10": "wind_speed",
    "RF_10": "humidity",
    "PP_10": "pressure",
}

HISTORY_FIELDS = {
    LOCAL_FIELD_MAP["temperature"],
    LOCAL_FIELD_MAP["precipitation"],
    LOCAL_FIELD_MAP["wind_speed"],
    LOCAL_FIELD_MAP["humidity"],
    LOCAL_FIELD_MAP["pressure"],
}


@dataclass(frozen=True)
class HistoricalPeriod:
    label: str
    start: datetime
    end: datetime


class ChatService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def answer(self, question: str) -> str:
        period = _parse_historical_period(question)
        if period is not None:
            return self._answer_historical(question, period)
        if _is_tomorrow_forecast_question(question):
            return self._answer_tomorrow(question)

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

    def _answer_tomorrow(self, question: str) -> str:
        target_date = (datetime.now(timezone.utc) + timedelta(days=1)).date()
        profiles = latest_profiles_for_date(daily_profile_path(self.settings), target_date)
        if not profiles:
            forecasts = ForecastCsvArchive(self.settings).read()
            generated_at = datetime.now(timezone.utc)
            profiles = [
                *profiles_for_forecasts(forecasts, source="dwd", generated_at=generated_at),
                *profiles_for_forecasts(
                    corrected_forecasts(forecasts, self.settings.model_dir),
                    source="local-corrected",
                    generated_at=generated_at,
                ),
            ]
            profiles = [item for item in profiles if item.target_date == target_date]
            append_profiles(daily_profile_path(self.settings), profiles)

        dwd_temperature = _profile_for(profiles, "dwd", "temperature")
        local_temperature = _profile_for(profiles, "local-corrected", "temperature")
        lines = [
            "Kurzantwort:",
            _tomorrow_headline(dwd_temperature, local_temperature),
            "",
            "Tagesverlauf morgen:",
            *_tomorrow_profile_lines(profiles),
            "",
            "Datenlage:",
            f"- Zieltag: {target_date.isoformat()} (UTC).",
            f"- Gespeicherte Tagesprofile: {len(profiles)}.",
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

    def _answer_historical(self, question: str, period: HistoricalPeriod) -> str:
        observations = WeatherStationCsvCache(self.settings).observations_between(
            period.start,
            period.end,
            fields=HISTORY_FIELDS,
        )
        preferred = [item for item in observations if item.measurement == self.settings.local_measurement]
        source = f"lokale CSV ({self.settings.local_measurement})"
        summary = _summarize_local_history(preferred or observations)
        if not summary.get("points"):
            source = "DWD-CDC-Historie"
            summary = _summarize_dwd_history(_dwd_history_rows_between(self.settings.dwd_data_path, period.start, period.end))

        lines = [
            "Kurzantwort:",
            _historical_headline(period, source, summary),
            "",
            "Datenlage:",
            f"- Zeitraum: {period.start:%d.%m.%Y} bis {(period.end):%d.%m.%Y} (UTC, Ende exklusiv).",
            f"- Quelle: {source}.",
            f"- Messpunkte: {int(summary.get('points', 0)) if summary else 0}.",
            "",
            "Historische Auswertung:",
            *_historical_lines(summary),
            "",
            f"Frage: {question}",
        ]
        return "\n".join(lines)


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


def _tomorrow_headline(dwd_temperature: DailyProfile | None, local_temperature: DailyProfile | None) -> str:
    if dwd_temperature is None:
        return "Fuer morgen liegt noch kein gespeichertes DWD-Tagesprofil vor. Bitte zuerst /archive ausfuehren."
    dwd = (
        "Laut DWD wird es morgen zwischen "
        f"{_format_metric(dwd_temperature.min_value)} und {_format_metric(dwd_temperature.max_value)} Grad Celsius. "
        f"Am heissesten wird es um {_time_label(dwd_temperature.max_at)}."
    )
    if local_temperature is None:
        return dwd + " Eine lokal korrigierte Prognose ist noch nicht verfuegbar; dafuer muessen Modelle trainiert sein."
    local = (
        " Lokal korrigiert liegt die Spanne zwischen "
        f"{_format_metric(local_temperature.min_value)} und {_format_metric(local_temperature.max_value)} Grad Celsius. "
        f"Lokal am heissesten um {_time_label(local_temperature.max_at)}."
    )
    return dwd + local


def _tomorrow_profile_lines(profiles: list[DailyProfile]) -> list[str]:
    if not profiles:
        return ["- Keine Tagesprofile gespeichert."]
    lines: list[str] = []
    for source in ("dwd", "local-corrected"):
        source_profiles = [item for item in profiles if item.source == source]
        if not source_profiles:
            continue
        label = "DWD" if source == "dwd" else "Lokal korrigiert"
        lines.append(f"- {label}:")
        for variable in ("temperature", "precipitation", "wind_speed"):
            profile = _profile_for(source_profiles, source, variable)
            if profile is None:
                continue
            if variable == "temperature":
                lines.append(
                    f"  Temperatur {_format_metric(profile.min_value)} bis {_format_metric(profile.max_value)} Grad Celsius; "
                    f"Maximum um {_time_label(profile.max_at)}."
                )
            elif variable == "precipitation":
                lines.append(
                    f"  Niederschlag {_format_metric(profile.min_value)} bis {_format_metric(profile.max_value)} mm je Forecastpunkt; "
                    f"Maximum um {_time_label(profile.max_at)}."
                )
            elif variable == "wind_speed":
                lines.append(
                    f"  Wind {_format_metric(profile.min_value)} bis {_format_metric(profile.max_value)} m/s; "
                    f"Maximum um {_time_label(profile.max_at)}."
                )
    return lines or ["- Keine Kernprofile fuer Temperatur, Niederschlag oder Wind gespeichert."]


def _profile_for(profiles: list[DailyProfile], source: str, variable: str) -> DailyProfile | None:
    candidates = [item for item in profiles if item.source == source and item.variable == variable]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.generated_at)


def _is_tomorrow_forecast_question(question: str) -> bool:
    normalized = _normalize_german_text(question)
    return "morgen" in normalized and any(
        token in normalized for token in ("wetter", "temperatur", "grad", "warm", "heiss", "prognose")
    )


def _time_label(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%H:%M UTC")


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


def _parse_historical_period(question: str) -> HistoricalPeriod | None:
    normalized = _normalize_german_text(question)
    pattern = r"\b(" + "|".join(sorted(MONTHS_DE, key=len, reverse=True)) + r")\s+(\d{4})\b"
    match = re.search(pattern, normalized)
    if not match:
        return None
    month_name = match.group(1)
    year = int(match.group(2))
    month = MONTHS_DE[month_name]
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    label = f"{_month_label(month)} {year}"
    return HistoricalPeriod(label=label, start=start, end=end)


def _month_label(month: int) -> str:
    labels = {
        1: "Januar",
        2: "Februar",
        3: "Maerz",
        4: "April",
        5: "Mai",
        6: "Juni",
        7: "Juli",
        8: "August",
        9: "September",
        10: "Oktober",
        11: "November",
        12: "Dezember",
    }
    return labels.get(month, str(month))


def _normalize_german_text(value: str) -> str:
    normalized = value.lower()
    replacements = (
        ("\u00e4", "ae"),
        ("\u00f6", "oe"),
        ("\u00fc", "ue"),
        ("\u00df", "ss"),
    )
    for source, replacement in replacements:
        normalized = normalized.replace(source, replacement)
    return normalized


def _summarize_local_history(observations: list[LocalObservation]) -> dict[str, object]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for observation in observations:
        value = _number_or_none(observation.value)
        if value is None:
            continue
        grouped[observation.variable].append(value)
    return _summarize_grouped_values(grouped)


def _summarize_dwd_history(rows: list[dict[str, str]]) -> dict[str, object]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        variable = DWD_HISTORY_FIELD_MAP.get(row.get("field", ""))
        value = _number_or_none(row.get("value", ""))
        if variable is None or value is None:
            continue
        grouped[variable].append(value)
    return _summarize_grouped_values(grouped)


def _summarize_grouped_values(grouped: dict[str, list[float]]) -> dict[str, object]:
    summary: dict[str, object] = {"points": sum(len(values) for values in grouped.values())}
    for variable, values in grouped.items():
        if not values:
            continue
        if variable == "precipitation":
            summary[variable] = {
                "count": len(values),
                "sum": sum(values),
                "max": max(values),
            }
            continue
        summary[variable] = {
            "count": len(values),
            "avg": sum(values) / len(values),
            "min": min(values),
            "max": max(values),
        }
    return summary


def _historical_headline(period: HistoricalPeriod, source: str, summary: dict[str, object]) -> str:
    if not summary or not summary.get("points"):
        return f"Fuer {period.label} liegen in lokaler CSV und DWD-Historie keine verwertbaren Messwerte vor."
    temperature = summary.get("temperature")
    precipitation = summary.get("precipitation")
    wind = summary.get("wind_speed")
    parts = [f"Fuer {period.label} wurden Messwerte {_source_phrase(source)} ausgewertet."]
    if isinstance(temperature, dict):
        parts.append(
            "Temperatur im Mittel "
            f"{_format_metric(temperature.get('avg'))} Grad Celsius, "
            f"Minimum {_format_metric(temperature.get('min'))}, Maximum {_format_metric(temperature.get('max'))}."
        )
    if isinstance(precipitation, dict):
        parts.append(f"Niederschlagssumme {_format_metric(precipitation.get('sum'))} mm.")
    if isinstance(wind, dict):
        parts.append(f"Wind im Mittel {_format_metric(wind.get('avg'))} m/s.")
    return " ".join(parts)


def _historical_lines(summary: dict[str, object]) -> list[str]:
    if not summary or not summary.get("points"):
        return ["- Keine passenden Messwerte gefunden."]
    lines: list[str] = []
    temperature = summary.get("temperature")
    precipitation = summary.get("precipitation")
    wind = summary.get("wind_speed")
    humidity = summary.get("humidity")
    pressure = summary.get("pressure")
    if isinstance(temperature, dict):
        lines.append(
            "- Temperatur: "
            f"avg {_format_metric(temperature.get('avg'))} Grad Celsius, "
            f"min {_format_metric(temperature.get('min'))}, "
            f"max {_format_metric(temperature.get('max'))} "
            f"({int(temperature.get('count', 0))} Werte)."
        )
    if isinstance(precipitation, dict):
        lines.append(
            "- Niederschlag: "
            f"Summe {_format_metric(precipitation.get('sum'))} mm, "
            f"max pro Messintervall {_format_metric(precipitation.get('max'))} mm "
            f"({int(precipitation.get('count', 0))} Werte)."
        )
    if isinstance(wind, dict):
        lines.append(
            "- Wind: "
            f"avg {_format_metric(wind.get('avg'))} m/s, "
            f"max {_format_metric(wind.get('max'))} m/s "
            f"({int(wind.get('count', 0))} Werte)."
        )
    if isinstance(humidity, dict):
        lines.append(f"- Luftfeuchtigkeit: avg {_format_metric(humidity.get('avg'))} %.")
    if isinstance(pressure, dict):
        lines.append(f"- Luftdruck: avg {_format_metric(pressure.get('avg'))} hPa.")
    return lines or ["- Keine Kernwerte fuer Temperatur, Niederschlag oder Wind gefunden."]


def _source_phrase(source: str) -> str:
    if source == "DWD-CDC-Historie":
        return "aus der DWD-CDC-Historie"
    if source.startswith("lokale CSV"):
        return f"aus der {source}"
    return f"aus {source}"


def _dwd_history_rows_between(path: Path, start: datetime, end: datetime) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    seen_observation = False
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("kind") != "observation":
                continue
            seen_observation = True
            row_time = _parse_row_time(row.get("time", ""))
            if row_time is None:
                continue
            if row_time < start:
                continue
            if row_time >= end:
                break
            if row.get("field") in DWD_HISTORY_FIELD_MAP:
                rows.append(row)
    return rows if seen_observation else []


def _parse_row_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _number_or_none(value: float | str | object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _format_metric(value: object) -> str:
    number = _number_or_none(value)
    if number is None:
        return "-"
    return f"{number:.2f}".rstrip("0").rstrip(".")
