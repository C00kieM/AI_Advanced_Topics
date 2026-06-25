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
            self._headline(status, forecasts, forecast_error),
            "",
            *_assistant_status_lines(status),
            *_forecast_lines(forecasts, forecast_error),
            *_local_interpretation(status, forecasts),
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
            _tomorrow_headline(dwd_temperature, local_temperature),
            "",
            *_tomorrow_profile_lines(profiles),
            "Ich nutze dafuer das zuletzt gespeicherte Tagesprofil.",
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
            _historical_headline(period, source, summary),
            "",
            f"Basis: {int(summary.get('points', 0)) if summary else 0} Messwerte aus {source}.",
        ]
        return "\n".join(lines)


def _assistant_status_lines(status) -> list[str]:
    lines: list[str] = []
    latest = max((item.time for item in status.local_latest), default=None)
    if latest:
        lines.append(f"Der neueste lokale Messwert ist vom {latest:%d.%m.%Y um %H:%M} UTC.")
    else:
        lines.append("Ich finde aktuell keine lokalen Messwerte im Cache.")
    if status.is_local_stale:
        lines.append("Die lokale Korrektur ist deshalb nur eingeschraenkt belastbar.")
    if status.warnings:
        lines.append(f"Hinweis: {status.warnings[0]}")
    return lines


def _forecast_lines(forecasts: list[ForecastPoint], forecast_error: str | None) -> list[str]:
    if forecast_error:
        return [
            f"DWD-Hinweis: {forecast_error}",
            "Temperatur: keine aktuelle DWD-Prognose verfuegbar.",
            "Wind: keine aktuelle DWD-Prognose verfuegbar.",
            "Niederschlag: keine aktuelle DWD-Prognose verfuegbar.",
        ]
    if not forecasts:
        return [
            "DWD liefert gerade keine verwertbare Prognose.",
            "Temperatur: keine aktuelle DWD-Prognose verfuegbar.",
            "Wind: keine aktuelle DWD-Prognose verfuegbar.",
            "Niederschlag: keine aktuelle DWD-Prognose verfuegbar.",
        ]
    grouped: dict[str, list[ForecastPoint]] = defaultdict(list)
    for item in forecasts:
        if item.variable in {"temperature", "precipitation", "wind_speed"}:
            grouped[item.variable].append(item)
    lines: list[str] = []
    temperature_items = sorted(grouped.get("temperature", []), key=lambda item: item.valid_at)
    if temperature_items:
        window = temperature_items[: min(6, len(temperature_items))]
        min_item = min(window, key=lambda item: item.value)
        max_item = max(window, key=lambda item: item.value)
        lines.append(
            "Temperatur: DWD liegt im Kurzfenster grob zwischen "
            f"{_format_temperature(min_item.value)} und {_format_temperature(max_item.value)}."
        )
    else:
        lines.append("Temperatur: im DWD-Kurzfenster nicht verfuegbar.")
    wind_items = grouped.get("wind_speed", [])
    if wind_items:
        maximum = max(item.value for item in wind_items[: min(6, len(wind_items))])
        lines.append(f"Wind: DWD sieht im Kurzfenster bis zu {_format_metric(maximum)} m/s.")
    else:
        lines.append("Wind: im DWD-Kurzfenster nicht verfuegbar.")
    precipitation_items = grouped.get("precipitation", [])
    if precipitation_items:
        maximum = max(item.value for item in precipitation_items[: min(6, len(precipitation_items))])
        if maximum <= 0:
            lines.append("Niederschlag: im DWD-Kurzfenster kein Niederschlag erwartet.")
        else:
            lines.append(f"Niederschlag: DWD sieht im Kurzfenster bis zu {_format_metric(maximum)} mm.")
    else:
        lines.append("Niederschlag: im DWD-Kurzfenster nicht verfuegbar.")
    return lines


def _tomorrow_headline(dwd_temperature: DailyProfile | None, local_temperature: DailyProfile | None) -> str:
    if dwd_temperature is None:
        return "Fuer morgen liegt noch kein gespeichertes DWD-Tagesprofil vor. Bitte zuerst /archive ausfuehren."
    dwd = f"Fuer morgen habe ich DWD-Prognose und lokale Korrektur zusammengefasst. DWD sieht das Maximum gegen {_time_label(dwd_temperature.max_at)}."
    if local_temperature is None:
        return dwd + " Eine lokal korrigierte Prognose ist noch nicht verfuegbar; dafuer muessen Modelle trainiert sein."
    return dwd + f" Lokal liegt das Maximum gegen {_time_label(local_temperature.max_at)}."


def _tomorrow_profile_lines(profiles: list[DailyProfile]) -> list[str]:
    if not profiles:
        return [
            "Temperatur: kein gespeichertes Tagesprofil verfuegbar.",
            "Wind: kein gespeichertes Tagesprofil verfuegbar.",
            "Niederschlag: kein gespeichertes Tagesprofil verfuegbar.",
        ]
    dwd_temperature = _profile_for(profiles, "dwd", "temperature")
    local_temperature = _profile_for(profiles, "local-corrected", "temperature")
    dwd_wind = _profile_for(profiles, "dwd", "wind_speed")
    local_wind = _profile_for(profiles, "local-corrected", "wind_speed")
    dwd_rain = _profile_for(profiles, "dwd", "precipitation")
    local_rain = _profile_for(profiles, "local-corrected", "precipitation")
    lines = [
        (
            "Temperatur: "
            f"DWD {_format_profile_temperature_range(dwd_temperature)}; "
            f"lokal korrigiert {_format_profile_temperature_range(local_temperature)}."
        ),
        (
            "Wind: "
            f"DWD {_format_profile_metric_range(dwd_wind, 'm/s')}; "
            f"lokal korrigiert {_format_profile_metric_range(local_wind, 'm/s')}."
        ),
        (
            "Niederschlag: "
            f"DWD {_format_profile_metric_range(dwd_rain, 'mm je Forecastpunkt')}; "
            f"lokal korrigiert {_format_profile_metric_range(local_rain, 'mm je Forecastpunkt')}."
        ),
    ]
    if _profile_for(profiles, "local-corrected", "temperature") is not None:
        lines.append("Die lokale Schaetzung basiert auf den trainierten Korrekturmodellen und kann bei veralteten Messdaten schwanken.")
    return lines


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
        lines.append(f"Lokale Temperatur: zuletzt {_format_temperature(temperature.value)}.")
    else:
        lines.append("Lokale Temperatur: kein aktueller Kernmesswert verfuegbar.")
    if wind:
        lines.append(f"Lokaler Wind: zuletzt {_format_value(wind.value)} m/s.")
    else:
        lines.append("Lokaler Wind: kein aktueller Kernmesswert verfuegbar.")
    if precipitation:
        lines.append(f"Lokaler Niederschlag: zuletzt {_format_value(precipitation.value)}.")
    else:
        lines.append("Lokaler Niederschlag: kein aktueller Kernmesswert verfuegbar.")
    if status.is_local_stale:
        lines.append("Ich wuerde die lokale Korrektur erst wieder staerker gewichten, wenn frische Stationsdaten ankommen.")
    elif forecasts:
        lines.append("Die lokale Korrektur wird mit mehr Forecast-vs-Ist-Paaren belastbarer.")
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
        return (
            f"Fuer {period.label} liegen in lokaler CSV und DWD-Historie keine verwertbaren Messwerte vor. "
            "Temperatur: nicht verfuegbar. Wind: nicht verfuegbar. Niederschlag: nicht verfuegbar."
        )
    temperature = summary.get("temperature")
    precipitation = summary.get("precipitation")
    wind = summary.get("wind_speed")
    parts = [f"Fuer {period.label} wurden Messwerte {_source_phrase(source)} ausgewertet."]
    if isinstance(temperature, dict):
        parts.append(
            "Temperatur im Mittel "
            f"{_format_temperature(temperature.get('avg'))}, "
            f"Minimum {_format_temperature(temperature.get('min'))}, Maximum {_format_temperature(temperature.get('max'))}."
        )
    else:
        parts.append("Temperatur: nicht verfuegbar.")
    if isinstance(wind, dict):
        parts.append(f"Wind im Mittel {_format_metric(wind.get('avg'))} m/s, maximal {_format_metric(wind.get('max'))} m/s.")
    else:
        parts.append("Wind: nicht verfuegbar.")
    if isinstance(precipitation, dict):
        parts.append(f"Niederschlagssumme {_format_metric(precipitation.get('sum'))} mm.")
    else:
        parts.append("Niederschlag: nicht verfuegbar.")
    return " ".join(parts)


def _historical_lines(summary: dict[str, object]) -> list[str]:
    if not summary or not summary.get("points"):
        return ["Ich habe dazu keine passenden Messwerte gefunden."]
    lines: list[str] = []
    temperature = summary.get("temperature")
    precipitation = summary.get("precipitation")
    wind = summary.get("wind_speed")
    humidity = summary.get("humidity")
    pressure = summary.get("pressure")
    if isinstance(temperature, dict):
        lines.append(
            "Temperatur: "
            f"im Mittel {_format_temperature(temperature.get('avg'))}, "
            f"Minimum {_format_temperature(temperature.get('min'))}, "
            f"Maximum {_format_temperature(temperature.get('max'))} "
            f"({int(temperature.get('count', 0))} Werte)."
        )
    if isinstance(precipitation, dict):
        lines.append(
            "Niederschlag: "
            f"Summe {_format_metric(precipitation.get('sum'))} mm, "
            f"maximal {_format_metric(precipitation.get('max'))} mm pro Messintervall "
            f"({int(precipitation.get('count', 0))} Werte)."
        )
    if isinstance(wind, dict):
        lines.append(
            "Wind: "
            f"im Mittel {_format_metric(wind.get('avg'))} m/s, "
            f"maximal {_format_metric(wind.get('max'))} m/s "
            f"({int(wind.get('count', 0))} Werte)."
        )
    if isinstance(humidity, dict):
        lines.append(f"Luftfeuchtigkeit: im Mittel {_format_metric(humidity.get('avg'))} %.")
    if isinstance(pressure, dict):
        lines.append(f"Luftdruck: im Mittel {_format_metric(pressure.get('avg'))} hPa.")
    return lines or ["Ich finde keine Kernwerte fuer Temperatur, Niederschlag oder Wind."]


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


def _format_profile_temperature_range(profile: DailyProfile | None) -> str:
    if profile is None:
        return "nicht verfuegbar"
    return f"{_format_temperature(profile.min_value)} bis {_format_temperature(profile.max_value)}"


def _format_profile_metric_range(profile: DailyProfile | None, unit: str) -> str:
    if profile is None:
        return "nicht verfuegbar"
    return f"{_format_metric(profile.min_value)} bis {_format_metric(profile.max_value)} {unit}"


def _format_temperature(value: object) -> str:
    return f"**{_format_metric(value)} Grad Celsius**"


def _format_metric(value: object) -> str:
    number = _number_or_none(value)
    if number is None:
        return "-"
    return f"{number:.2f}".rstrip("0").rstrip(".")
