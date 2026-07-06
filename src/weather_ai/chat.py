from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import calendar
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
from .local_cache import WeatherStationCsvCache, read_cache_rows
from .models import ForecastPoint, LOCAL_FIELD_MAP, LocalObservation
from .mosmix import MosmixClient
from .stations import StationScope, scoped_model_dir, station_scope_from_question
from .strunde import StrundeService


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

try:
    LOCAL_TZ = ZoneInfo("Europe/Berlin")
except Exception:  # noqa: BLE001 - Windows may not ship IANA tzdata.
    LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone(timedelta(hours=1))


@dataclass(frozen=True)
class HistoricalPeriod:
    label: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class DataRange:
    start: datetime | None
    end: datetime | None


class ChatService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def answer(self, question: str) -> str:
        now = _now_utc()
        if _is_strunde_question(question):
            strunde_period = _strunde_period(question, now)
            return StrundeService(self.settings).answer(question, strunde_period)
        station_scope = station_scope_from_question(self.settings, question)
        if station_scope.is_ambiguous:
            return _ambiguous_station_answer(station_scope)
        date_period = _parse_specific_date_period(question)
        if date_period is not None:
            if date_period.start > now:
                return _future_limit_answer(date_period, _add_months(now, 6))
            return self._answer_historical(question, date_period, station_scope)
        relative_periods = _parse_relative_past_periods(question, now)
        if relative_periods:
            return "\n\n".join(self._answer_historical(question, period, station_scope) for period in relative_periods)
        period = _parse_month_period(question, now)
        if period is not None:
            if period.end > now:
                return self._answer_future_month(question, period, now, station_scope)
            return self._answer_historical(question, period, station_scope)
        year_period = _parse_year_period(question, now)
        if year_period is not None:
            if year_period.end > now:
                return _future_limit_answer(year_period, _add_months(now, 6))
            return self._answer_historical(question, year_period, station_scope)
        if _is_tomorrow_forecast_question(question):
            return self._answer_tomorrow(question, station_scope)

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

    def _answer_tomorrow(self, question: str, station_scope: StationScope) -> str:
        target_date = (datetime.now(timezone.utc) + timedelta(days=1)).date()
        profiles = [] if station_scope.is_single else latest_profiles_for_date(daily_profile_path(self.settings), target_date)
        if not profiles:
            forecasts = ForecastCsvArchive(self.settings).read()
            generated_at = datetime.now(timezone.utc)
            profiles = [
                *profiles_for_forecasts(forecasts, source="dwd", generated_at=generated_at),
                *profiles_for_forecasts(
                    corrected_forecasts(forecasts, scoped_model_dir(self.settings, station_scope)),
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
            return "Aktuell fehlen mir frische lokale Messwerte und eine aktuelle DWD-Prognose."
        if status.is_local_stale:
            return "Ich kann die DWD-Prognose nennen; die lokale Einschaetzung ist wegen aelterer Messwerte nur eingeschraenkt belastbar."
        if forecast_error:
            return "Ich sehe lokale Messwerte, aber gerade keine aktuelle DWD-Prognose."
        if forecasts:
            return "Fuer die naechsten verfuegbaren Prognosezeiten sieht das Wetter so aus:"
        return "Es liegen noch nicht genug Daten fuer eine belastbare Antwort vor."

    def _answer_future_month(self, question: str, period: HistoricalPeriod, now: datetime, station_scope: StationScope) -> str:
        limit = _add_months(now, 6)
        if period.start > limit:
            return _future_limit_answer(period, limit)

        status = build_status(self.settings)
        forecasts, forecast_error = self._fetch_forecasts()
        climatology = _summarize_month_climatology(self.settings, period.start.month, now, station_scope)
        lines = [
            (
                f"Eine verlaessliche Vorhersage fuer den gesamten {period.label} ist "
                f"am {_format_date_local(now)} noch nicht moeglich. Fuer eine Monatsplanung kann ich nur "
                "typische Werte aus den gespeicherten Daten und die aktuelle Kurzfristprognose nutzen."
            ),
            "",
            *_climatology_lines(period, climatology),
            "",
            "Aktuelle DWD-Kurzfristprognose:",
            *_forecast_lines(forecasts, forecast_error),
            "",
            *_latest_local_lines(status),
        ]
        return "\n".join(lines)

    def _answer_historical(self, question: str, period: HistoricalPeriod, station_scope: StationScope) -> str:
        history_cutoff = _history_cutoff(_now_utc())
        if period.start < history_cutoff:
            return _history_limit_answer(period, history_cutoff)

        local_error = None
        dwd_error = None
        try:
            local_observations = WeatherStationCsvCache(self.settings).observations_between(
                period.start,
                period.end,
                fields=HISTORY_FIELDS,
                measurements=station_scope.measurements,
            )
        except Exception as exc:  # noqa: BLE001 - chat should still answer with the remaining source.
            local_observations = []
            local_error = str(exc)
        local_summary = _summarize_local_history(local_observations, filter_outliers=not station_scope.is_single)
        try:
            dwd_summary = _summarize_dwd_history_between(self.settings.dwd_data_path, period.start, period.end)
        except Exception as exc:  # noqa: BLE001 - chat should still answer with local data.
            dwd_summary = {"points": 0}
            dwd_error = str(exc)
        return _historical_answer(
            period,
            _local_scope_label(station_scope),
            local_summary,
            dwd_summary,
            local_error=local_error,
            dwd_error=dwd_error,
        )


def _assistant_status_lines(status) -> list[str]:
    lines: list[str] = []
    latest = max((item.time for item in status.local_latest), default=None)
    if latest:
        lines.append(f"Der neueste lokale Messwert ist vom {_format_datetime_local(latest)}.")
    else:
        lines.append("Ich finde aktuell keine lokalen Messwerte im Cache.")
    if status.is_local_stale:
        lines.append("Die lokale Einschaetzung ist deshalb nur eingeschraenkt belastbar.")
    if status.warnings:
        lines.append(f"Hinweis: {status.warnings[0]}")
    return lines


def _local_scope_label(station_scope: StationScope) -> str:
    if station_scope.is_single:
        return f"Station {station_scope.label}"
    return "alle Stationen"


def _ambiguous_station_answer(station_scope: StationScope) -> str:
    options = ", ".join(f"{item.label} ({item.measurement})" for item in station_scope.matches)
    return "\n".join(
        [
            "Ich kann die Station nicht eindeutig zuordnen.",
            f"Meinst du eine dieser Stationen: {options}?",
            "",
            "Temperatur: nicht ausgewertet.",
            "Wind: nicht ausgewertet.",
            "Niederschlag: nicht ausgewertet.",
        ]
    )


def _latest_local_lines(status) -> list[str]:
    latest_by_variable = _latest_observations_by_variable(status.local_latest)
    lines: list[str] = ["Lokale Messwerte:"]
    temperature = latest_by_variable.get("temperature")
    wind = latest_by_variable.get("wind_speed")
    precipitation = latest_by_variable.get("precipitation")
    if temperature:
        lines.append(f"Temperatur: zuletzt {_format_temperature(temperature.value)} am {_format_datetime_local(temperature.time)}.")
    else:
        lines.append("Temperatur: kein aktueller lokaler Messwert verfuegbar.")
    if wind:
        lines.append(f"Wind: zuletzt {_format_metric(wind.value)} m/s am {_format_datetime_local(wind.time)}.")
    else:
        lines.append("Wind: kein aktueller lokaler Messwert verfuegbar.")
    if precipitation:
        lines.append(f"Niederschlag: zuletzt {_format_metric(precipitation.value)} mm am {_format_datetime_local(precipitation.time)}.")
    else:
        lines.append("Niederschlag: kein aktueller lokaler Messwert verfuegbar.")
    if status.is_local_stale:
        lines.append("Diese lokalen Werte sind aelter und passen moeglicherweise nicht mehr zur aktuellen Wetterlage.")
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
            "Temperatur: DWD erwartet etwa "
            f"{_format_temperature_range(min_item.value, max_item.value)}."
        )
    else:
        lines.append("Temperatur: in der aktuellen DWD-Prognose nicht verfuegbar.")
    wind_items = grouped.get("wind_speed", [])
    if wind_items:
        maximum = max(item.value for item in wind_items[: min(6, len(wind_items))])
        lines.append(f"Wind: DWD erwartet bis zu {_format_metric(maximum)} m/s.")
    else:
        lines.append("Wind: in der aktuellen DWD-Prognose nicht verfuegbar.")
    precipitation_items = grouped.get("precipitation", [])
    if precipitation_items:
        maximum = max(item.value for item in precipitation_items[: min(6, len(precipitation_items))])
        if maximum <= 0:
            lines.append("Niederschlag: kein Niederschlag erwartet.")
        else:
            lines.append(f"Niederschlag: DWD erwartet bis zu {_format_metric(maximum)} mm.")
    else:
        lines.append("Niederschlag: in der aktuellen DWD-Prognose nicht verfuegbar.")
    return lines


def _tomorrow_headline(dwd_temperature: DailyProfile | None, local_temperature: DailyProfile | None) -> str:
    if dwd_temperature is None:
        return "Fuer morgen liegt noch kein gespeichertes DWD-Tagesprofil vor. Bitte zuerst /archive ausfuehren."
    dwd = f"Fuer morgen sieht DWD den waermsten Zeitpunkt gegen {_time_label(dwd_temperature.max_at)}."
    if local_temperature is None:
        return dwd + " Eine lokale Korrektur ist noch nicht verfuegbar."
    return dwd + f" Lokal liegt der waermste Zeitpunkt voraussichtlich gegen {_time_label(local_temperature.max_at)}."


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
        lines.append("Die lokale Schaetzung ist eine Korrektur auf Basis der bisher gesammelten lokalen Messwerte.")
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
    return value.astimezone(LOCAL_TZ).strftime("%H:%M Uhr")


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
        lines.append(f"Lokaler Wind: zuletzt {_format_metric(wind.value)} m/s.")
    else:
        lines.append("Lokaler Wind: kein aktueller Kernmesswert verfuegbar.")
    if precipitation:
        lines.append(f"Lokaler Niederschlag: zuletzt {_format_metric(precipitation.value)} mm.")
    else:
        lines.append("Lokaler Niederschlag: kein aktueller Kernmesswert verfuegbar.")
    if status.is_local_stale:
        lines.append("Ich gewichte die lokale Einschaetzung erst wieder staerker, wenn frische Stationsdaten ankommen.")
    elif forecasts:
        lines.append("Die lokale Einschaetzung wird mit mehr passenden DWD- und Stationsdaten belastbarer.")
    return lines


def _latest_observations_by_variable(observations: list[LocalObservation]) -> dict[str, LocalObservation]:
    latest: dict[str, LocalObservation] = {}
    for observation in observations:
        current = latest.get(observation.variable)
        if current is None or observation.time > current.time:
            latest[observation.variable] = observation
    return latest


def _parse_specific_date_period(question: str) -> HistoricalPeriod | None:
    normalized = _normalize_german_text(question)
    match = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", normalized)
    if match:
        return _date_period(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    match = re.search(r"\b(20\d{2}|19\d{2})-(\d{1,2})-(\d{1,2})\b", normalized)
    if match:
        return _date_period(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    month_names = "|".join(sorted(MONTHS_DE, key=len, reverse=True))
    match = re.search(rf"\b(\d{{1,2}})\.\s*({month_names})\s+(\d{{4}})\b", normalized)
    if match:
        return _date_period(int(match.group(3)), MONTHS_DE[match.group(2)], int(match.group(1)))
    return None


def _parse_month_period(question: str, now: datetime) -> HistoricalPeriod | None:
    normalized = _normalize_german_text(question)
    pattern = r"\b(" + "|".join(sorted(MONTHS_DE, key=len, reverse=True)) + r")\s+(\d{4})\b"
    match = re.search(pattern, normalized)
    if match:
        month_name = match.group(1)
        year = int(match.group(2))
        return _month_period(year, MONTHS_DE[month_name])

    month_match = re.search(r"\b(" + "|".join(sorted(MONTHS_DE, key=len, reverse=True)) + r")\b", normalized)
    if not month_match:
        return None
    if not any(token in normalized for token in ("wetter", "durchschnitt", "monat", "sommer", "warm", "regen", "wind")):
        return None
    month = MONTHS_DE[month_match.group(1)]
    year = now.astimezone(LOCAL_TZ).year
    if "naechst" in normalized or "nachst" in normalized:
        year += 1
    return _month_period(year, month)


def _parse_relative_past_period(question: str, now: datetime) -> HistoricalPeriod | None:
    periods = _parse_relative_past_periods(question, now)
    return periods[0] if periods else None


def _parse_relative_past_periods(question: str, now: datetime) -> list[HistoricalPeriod]:
    normalized = _normalize_german_text(question)
    local_today = now.astimezone(LOCAL_TZ).date()
    candidates: list[tuple[int, HistoricalPeriod]] = []
    for match in re.finditer(r"\bvorgestern\b", normalized):
        candidates.append((match.start(), _day_period(local_today - timedelta(days=2), "vorgestern")))
    for match in re.finditer(r"\bgestern\b", normalized):
        candidates.append((match.start(), _day_period(local_today - timedelta(days=1), "gestern")))
    week_words = {
        "einer": 1,
        "eine": 1,
        "ein": 1,
        "1": 1,
        "zwei": 2,
        "2": 2,
        "drei": 3,
        "3": 3,
        "vier": 4,
        "4": 4,
        "fuenf": 5,
        "5": 5,
        "sechs": 6,
        "6": 6,
        "sieben": 7,
        "7": 7,
        "acht": 8,
        "8": 8,
        "neun": 9,
        "9": 9,
        "zehn": 10,
        "10": 10,
    }
    week_pattern = r"\bvor\s+(einer|eine|ein|1|zwei|2|drei|3|vier|4|fuenf|5|sechs|6|sieben|7|acht|8|neun|9|zehn|10)\s+wochen?\b"
    for match in re.finditer(week_pattern, normalized):
        weeks = week_words[match.group(1)]
        label = "vor einer Woche" if weeks == 1 else f"vor {weeks} Wochen"
        candidates.append((match.start(), _day_period(local_today - timedelta(days=7 * weeks), label)))

    match = re.search(r"\bvor\s+(\d{1,3})\s+jahr", normalized)
    if match:
        years = int(match.group(1))
        year = now.astimezone(LOCAL_TZ).year - years
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        candidates.append((match.start(), HistoricalPeriod(label=f"das Jahr {year}", start=start, end=end)))

    seen: set[tuple[str, datetime, datetime]] = set()
    periods: list[HistoricalPeriod] = []
    for _, period in sorted(candidates, key=lambda item: item[0]):
        key = (period.label, period.start, period.end)
        if key in seen:
            continue
        seen.add(key)
        periods.append(period)
    return periods


def _parse_year_period(question: str, now: datetime) -> HistoricalPeriod | None:
    normalized = _normalize_german_text(question)
    if not any(token in normalized for token in ("wetter", "temperatur", "wind", "regen", "niederschlag", "warm", "kalt")):
        return None
    without_dates = re.sub(r"\b\d{1,2}\.\d{1,2}\.(?:19\d{2}|20\d{2})\b", " ", normalized)
    without_dates = re.sub(r"\b(?:19\d{2}|20\d{2})-\d{1,2}-\d{1,2}\b", " ", without_dates)
    match = re.search(r"\b(19\d{2}|20\d{2})\b", without_dates)
    if not match:
        return None
    year = int(match.group(1))
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    return HistoricalPeriod(label=f"das Jahr {year}", start=start, end=end)


def _date_period(year: int, month: int, day: int) -> HistoricalPeriod | None:
    try:
        local_start = datetime(year, month, day, tzinfo=LOCAL_TZ)
    except ValueError:
        return None
    return HistoricalPeriod(
        label=f"den {day:02d}.{month:02d}.{year}",
        start=local_start.astimezone(timezone.utc),
        end=(local_start + timedelta(days=1)).astimezone(timezone.utc),
    )


def _is_strunde_question(question: str) -> bool:
    normalized = _normalize_german_text(question)
    return any(token in normalized for token in ("strunde", "strunder", "pegel", "wasserstand"))


def _strunde_period(question: str, now: datetime) -> HistoricalPeriod | None:
    date_period = _parse_specific_date_period(question)
    if date_period is not None:
        return date_period
    relative_periods = _parse_relative_past_periods(question, now)
    if relative_periods:
        return relative_periods[0]
    month_period = _parse_month_period(question, now)
    if month_period is not None and month_period.end <= now:
        return month_period
    year_period = _parse_year_period(question, now)
    if year_period is not None and year_period.end <= now:
        return year_period
    return None


def _day_period(day, label: str) -> HistoricalPeriod:
    start_local = datetime(day.year, day.month, day.day, tzinfo=LOCAL_TZ)
    end_local = start_local + timedelta(days=1)
    return HistoricalPeriod(
        label=f"{label} ({start_local:%d.%m.%Y})",
        start=start_local.astimezone(timezone.utc),
        end=end_local.astimezone(timezone.utc),
    )


def _month_period(year: int, month: int) -> HistoricalPeriod:
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


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _history_cutoff(now: datetime) -> datetime:
    return _add_months(now, -36)


def _history_limit_answer(period: HistoricalPeriod, cutoff: datetime) -> str:
    return "\n".join(
        [
            f"Fuer {period.label} kann ich keine belastbare Auswertung liefern.",
            f"Ich beantworte historische Wetterfragen maximal drei Jahre zurueck, aktuell also ab {_format_date_local(cutoff)}.",
            "",
            "Temperatur: nicht verfuegbar.",
            "Wind: nicht verfuegbar.",
            "Niederschlag: nicht verfuegbar.",
        ]
    )


def _future_limit_answer(period: HistoricalPeriod, limit: datetime) -> str:
    return "\n".join(
        [
            (
                f"So weit nach vorne kann ich das Wetter nicht serioes einschaetzen. "
                f"Ich begrenze Zukunftsfragen auf maximal sechs Monate; aktuell also bis {_format_date_local(limit)}."
            ),
            "",
            f"Fuer {period.label} wuerde ich sonst Werte erfinden, und das mache ich nicht.",
            "",
            "Temperatur: nicht verfuegbar.",
            "Wind: nicht verfuegbar.",
            "Niederschlag: nicht verfuegbar.",
        ]
    )


def _available_data_range(settings: Settings) -> DataRange:
    times: list[datetime] = []
    for row in read_cache_rows(settings.local_cache_path):
        row_time = _parse_row_time(row.get("time", ""))
        if row_time is not None:
            times.append(row_time)
    times.extend(_dwd_observation_times(settings.dwd_data_path))
    if not times:
        return DataRange(start=None, end=None)
    return DataRange(start=min(times), end=max(times))


def _dwd_observation_times(path: Path) -> list[datetime]:
    if not path.exists():
        return []
    times: list[datetime] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("kind") != "observation":
                continue
            row_time = _parse_row_time(row.get("time", ""))
            if row_time is not None:
                times.append(row_time)
    return times


def _summarize_month_climatology(settings: Settings, month: int, now: datetime, station_scope: StationScope) -> dict[str, object]:
    data_range = _available_data_range(settings)
    if data_range.start is None or data_range.end is None:
        return {"source": "", "summary": {"points": 0}, "range": data_range}
    end = min(data_range.end, now)
    local = WeatherStationCsvCache(settings).observations_between(
        data_range.start,
        end,
        fields=HISTORY_FIELDS,
        measurements=station_scope.measurements,
    )
    local_same_month = [item for item in local if item.time.month == month and item.time < now]
    if local_same_month:
        return {
            "source": f"lokalen Messdaten ({_local_scope_label(station_scope)})",
            "summary": _summarize_local_history(local_same_month, filter_outliers=not station_scope.is_single),
            "range": data_range,
        }

    rows = _dwd_history_rows_for_month(settings.dwd_data_path, month, now)
    return {
        "source": "DWD-CDC-Historie",
        "summary": _summarize_dwd_history(rows),
        "range": data_range,
    }


def _dwd_history_rows_for_month(path: Path, month: int, before: datetime) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("kind") != "observation" or row.get("field") not in DWD_HISTORY_FIELD_MAP:
                continue
            row_time = _parse_row_time(row.get("time", ""))
            if row_time is None or row_time >= before or row_time.month != month:
                continue
            rows.append(row)
    return rows


def _climatology_lines(period: HistoricalPeriod, payload: dict[str, object]) -> list[str]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    source = str(payload.get("source") or "den gespeicherten Daten")
    if not summary or not summary.get("points"):
        return [
            f"Fuer typische Werte im {period.label} habe ich in den gespeicherten Daten noch keine passende Grundlage.",
            "Temperatur: nicht verfuegbar.",
            "Wind: nicht verfuegbar.",
            "Niederschlag: nicht verfuegbar.",
        ]
    return [
        f"Typische Werte fuer {_month_label(period.start.month)} aus {source}:",
        *_summary_weather_lines(summary, precipitation_label="Niederschlag: Summe in den vorhandenen Monatsdaten"),
    ]


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


def _summarize_local_history(observations: list[LocalObservation], filter_outliers: bool = True) -> dict[str, object]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for observation in observations:
        value = _number_or_none(observation.value)
        if value is None:
            continue
        grouped[observation.variable].append(value)
    if filter_outliers:
        grouped = {variable: _filter_values(variable, values) for variable, values in grouped.items()}
    return _summarize_grouped_values(grouped)


def _filter_values(variable: str, values: list[float]) -> list[float]:
    plausible = {
        "temperature": (-45.0, 55.0),
        "precipitation": (0.0, 300.0),
        "wind_speed": (0.0, 70.0),
        "humidity": (0.0, 100.0),
        "pressure": (850.0, 1100.0),
    }
    low, high = plausible.get(variable, (-float("inf"), float("inf")))
    filtered = [value for value in values if low <= value <= high]
    if len(filtered) < 12:
        return filtered
    ordered = sorted(filtered)
    q1 = ordered[len(ordered) // 4]
    q3 = ordered[(len(ordered) * 3) // 4]
    iqr = q3 - q1
    if iqr <= 0:
        return filtered
    inner_low = q1 - 3 * iqr
    inner_high = q3 + 3 * iqr
    return [value for value in filtered if inner_low <= value <= inner_high]


def _summarize_dwd_history(rows: list[dict[str, str]]) -> dict[str, object]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        variable = DWD_HISTORY_FIELD_MAP.get(row.get("field", ""))
        value = _number_or_none(row.get("value", ""))
        if variable is None or value is None:
            continue
        grouped[variable].append(value)
    return _summarize_grouped_values(grouped)


def _summarize_dwd_history_between(path: Path, start: datetime, end: datetime) -> dict[str, object]:
    if not path.exists():
        return {"points": 0}
    return _summarize_dwd_history(_dwd_history_rows_between(path, start, end))


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


def _summary_weather_lines(summary: dict[str, object], precipitation_label: str) -> list[str]:
    temperature = summary.get("temperature")
    precipitation = summary.get("precipitation")
    wind = summary.get("wind_speed")
    lines: list[str] = []
    if isinstance(temperature, dict):
        lines.append(
            "Temperatur: im Mittel "
            f"{_format_temperature(temperature.get('avg'))}, "
            f"Spanne {_format_temperature_range(temperature.get('min'), temperature.get('max'))}."
        )
    else:
        lines.append("Temperatur: nicht verfuegbar.")
    if isinstance(wind, dict):
        lines.append(
            f"Wind: im Mittel {_format_metric(wind.get('avg'))} m/s, "
            f"maximal {_format_metric(wind.get('max'))} m/s."
        )
    else:
        lines.append("Wind: nicht verfuegbar.")
    if isinstance(precipitation, dict):
        lines.append(f"{precipitation_label}: {_format_metric(precipitation.get('sum'))} mm.")
    else:
        lines.append("Niederschlag: nicht verfuegbar.")
    return lines


def _source_missing_lines(source_label: str, error: str | None = None) -> list[str]:
    lines = [
        f"{source_label}: Fuer diesen Zeitraum fehlen verwertbare Daten.",
        "Temperatur: nicht verfuegbar.",
        "Wind: nicht verfuegbar.",
        "Niederschlag: nicht verfuegbar.",
    ]
    if error:
        lines.append(f"Hinweis: {source_label} konnte nicht gelesen werden: {error}")
    return lines


def _historical_answer(
    period: HistoricalPeriod,
    local_label_text: str,
    local_summary: dict[str, object],
    dwd_summary: dict[str, object],
    local_error: str | None = None,
    dwd_error: str | None = None,
) -> str:
    local_points = int(local_summary.get("points", 0) or 0)
    dwd_points = int(dwd_summary.get("points", 0) or 0)
    local_label = f"Lokale Daten ({local_label_text})"
    if not local_points and not dwd_points:
        return "\n".join(
            [
                f"Fuer {period.label} habe ich weder lokale Messwerte noch DWD-Historiendaten gefunden.",
                "",
                *_source_missing_lines(local_label, local_error),
                "",
                *_source_missing_lines("DWD-Historie", dwd_error),
            ]
        )

    lines = [f"Fuer {period.label} habe ich die gespeicherten Wetterdaten ausgewertet."]
    if not local_points or not dwd_points:
        lines.append("Hinweis: Fuer eine der beiden Quellen fehlen in diesem Zeitraum Daten.")
    lines.append("")

    if local_points:
        lines.extend(
            [
                f"{local_label}:",
                *_summary_weather_lines(local_summary, precipitation_label="Niederschlag: Summe im Zeitraum"),
                f"Basis: {local_points} Messwerte.",
            ]
        )
    else:
        lines.extend(_source_missing_lines(local_label, local_error))

    lines.append("")
    if dwd_points:
        lines.extend(
            [
                "DWD-Historie:",
                *_summary_weather_lines(dwd_summary, precipitation_label="Niederschlag: Summe im Zeitraum"),
                f"Basis: {dwd_points} Messwerte.",
            ]
        )
    else:
        lines.extend(_source_missing_lines("DWD-Historie", dwd_error))

    return "\n".join(lines)


def _dwd_history_rows_between(path: Path, start: datetime, end: datetime) -> list[dict[str, str]]:
    if not path.exists():
        return []
    if path.stat().st_size >= 40_000_000:
        return _dwd_history_rows_between_large_file(path, start, end)
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


def _dwd_history_rows_between_large_file(path: Path, start: datetime, end: datetime) -> list[dict[str, str]]:
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    rows: list[dict[str, str]] = []
    with path.open("rb") as handle:
        header_line = handle.readline().decode("utf-8", errors="replace")
        columns = next(csv.reader([header_line]))
        first_data_pos = handle.tell()
        file_size = path.stat().st_size
        low = first_data_pos
        high = file_size
        while low < high:
            mid = (low + high) // 2
            row_pos, row = _read_csv_row_at_or_after(handle, columns, mid)
            if row is None or row_pos is None:
                high = mid
                continue
            row_time = _parse_row_time(row.get("time", ""))
            if row.get("kind") != "observation" or row_time is None or row_time < start:
                low = max(row_pos + 1, mid + 1)
            else:
                high = mid

        handle.seek(low)
        if low > first_data_pos:
            handle.readline()
        for raw in handle:
            text = raw.decode("utf-8", errors="replace")
            parsed = next(csv.reader([text]), [])
            if len(parsed) < len(columns):
                continue
            row = {column: parsed[index] for index, column in enumerate(columns)}
            if row.get("kind") != "observation":
                continue
            row_time = _parse_row_time(row.get("time", ""))
            if row_time is None:
                continue
            if row_time < start:
                continue
            if row_time >= end:
                break
            if row.get("field") in DWD_HISTORY_FIELD_MAP:
                rows.append(row)
    return rows


def _read_csv_row_at_or_after(handle, columns: list[str], position: int):
    handle.seek(position)
    if position > 0:
        handle.readline()
    row_pos = handle.tell()
    raw = handle.readline()
    if not raw:
        return None, None
    text = raw.decode("utf-8", errors="replace")
    parsed = next(csv.reader([text]), [])
    if len(parsed) < len(columns):
        return row_pos, None
    return row_pos, {column: parsed[index] for index, column in enumerate(columns)}


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
    return _format_temperature_range(profile.min_value, profile.max_value)


def _format_profile_metric_range(profile: DailyProfile | None, unit: str) -> str:
    if profile is None:
        return "nicht verfuegbar"
    return f"{_format_metric(profile.min_value)} bis {_format_metric(profile.max_value)} {unit}"


def _format_temperature(value: object) -> str:
    return f"**{_format_metric(value)} °C**"


def _format_temperature_range(min_value: object, max_value: object) -> str:
    minimum = _number_or_none(min_value)
    maximum = _number_or_none(max_value)
    if minimum is None or maximum is None:
        return "nicht verfuegbar"
    if abs(minimum - maximum) < 0.05:
        return _format_temperature(minimum)
    return f"**{_format_metric(minimum, decimals=0)} bis {_format_metric(maximum, decimals=0)} °C**"


def _format_date_local(value: datetime) -> str:
    return value.astimezone(LOCAL_TZ).strftime("%d.%m.%Y")


def _format_datetime_local(value: datetime) -> str:
    return value.astimezone(LOCAL_TZ).strftime("%d.%m.%Y um %H:%M Uhr")


def _format_metric(value: object, decimals: int = 1) -> str:
    number = _number_or_none(value)
    if number is None:
        return "-"
    text = f"{number:.{decimals}f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")
