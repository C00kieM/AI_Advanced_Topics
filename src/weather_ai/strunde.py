from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import sqrt
from statistics import mean

from .config import Settings
from .forecast_archive import ForecastCsvArchive
from .local_cache import WeatherStationCsvCache
from .models import ForecastPoint, LOCAL_FIELD_MAP, LocalObservation
from .strunde_cache import StrundeLevelCsvCache, StrundeLevelObservation


@dataclass(frozen=True)
class RainLevelModel:
    lag_hours: int
    rain_window_hours: int
    response_hours: int
    correlation: float
    slope_cm_per_mm: float
    intercept_cm: float
    samples: int

    @property
    def usable(self) -> bool:
        return self.samples >= 6 and abs(self.correlation) >= 0.2 and self.slope_cm_per_mm > 0


class StrundeService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.level_cache = StrundeLevelCsvCache(settings)
        self.weather_cache = WeatherStationCsvCache(settings)
        self.forecast_archive = ForecastCsvArchive(settings)

    def answer(self, question: str, period=None) -> str:
        normalized = _normalize(question)
        if any(token in normalized for token in ("morgen", "naechste", "naechsten", "nachste", "woche", "prognose", "vorhersage", "steigen", "anstieg")):
            return self.forecast_answer(question)
        if period is not None:
            if period.start > datetime.now(timezone.utc):
                horizon_hours = max(24, min(168, int((period.end - datetime.now(timezone.utc)).total_seconds() // 3600)))
                return self.forecast_answer(question, horizon_hours=horizon_hours)
            return self.history_answer(period.label, period.start, period.end)
        if any(token in normalized for token in ("aktuell", "jetzt", "momentan", "hoch")):
            return self.current_answer()
        return self.current_answer()

    def current_answer(self) -> str:
        latest = self.level_cache.latest()
        if latest is None:
            return _missing_level_answer()
        model = self.rain_level_model()
        lines = [
            f"Die Strunde liegt aktuell bei {_format_level(latest.value_cm)}.",
            f"Letzter Pegelwert: {_format_datetime(latest.time)}.",
            "",
            _model_context_line(model),
        ]
        return "\n".join(lines)

    def history_answer(self, label: str, start: datetime, end: datetime) -> str:
        levels = self.level_cache.observations_between(start, end)
        if not levels:
            return "\n".join(
                [
                    f"Fuer {label} habe ich keine gespeicherten Strunde-Pegeldaten gefunden.",
                    "Pegel: nicht verfuegbar.",
                    "Niederschlag: nicht ausgewertet.",
                    "Prognose: nicht belastbar.",
                ]
            )
        rain = self._rain_between(start, end)
        level_values = [item.value_cm for item in levels]
        rain_sum = sum(_numeric(item.value) for item in rain)
        lines = [
            f"Fuer {label} habe ich die gespeicherten Strunde-Pegeldaten ausgewertet.",
            "",
            f"Pegel: im Mittel {_format_level(mean(level_values))}, Spanne {_format_level(min(level_values))} bis {_format_level(max(level_values))}.",
            f"Niederschlag: {_format_metric(rain_sum)} mm im gleichen Zeitraum.",
            f"Basis: {len(levels)} Pegelwerte und {len(rain)} Niederschlagswerte.",
        ]
        model = self.rain_level_model()
        lines.append(_model_context_line(model))
        return "\n".join(lines)

    def forecast_answer(self, question: str, horizon_hours: int | None = None) -> str:
        latest = self.level_cache.latest()
        if latest is None:
            return _missing_level_answer()
        horizon_hours = horizon_hours or (168 if "woche" in _normalize(question) else 24)
        forecasts = self._precipitation_forecasts(horizon_hours)
        expected_rain = sum(max(0.0, item.value) for item in forecasts)
        model = self.rain_level_model()
        rain_line = (
            f"Niederschlag: DWD erwartet in den naechsten {horizon_hours} Stunden etwa {_format_metric(expected_rain)} mm."
            if forecasts
            else "Niederschlag: keine gespeicherte DWD-Niederschlagsprognose fuer diesen Zeitraum gefunden."
        )
        if not forecasts:
            return "\n".join(
                [
                    f"Die Strunde liegt aktuell bei {_format_level(latest.value_cm)}.",
                    "Eine Pegelprognose ist ohne gespeicherte DWD-Niederschlagsprognose fuer den Zeitraum nicht belastbar.",
                    rain_line,
                    _model_context_line(model),
                ]
            )
        if not model.usable:
            return "\n".join(
                [
                    f"Die Strunde liegt aktuell bei {_format_level(latest.value_cm)}.",
                    "Eine belastbare Pegelprognose ist noch nicht moeglich, weil zu wenige passende Pegel- und Niederschlagsdaten vorhanden sind.",
                    rain_line,
                    _model_context_line(model),
                ]
            )
        rise = max(0.0, model.intercept_cm + expected_rain * model.slope_cm_per_mm)
        forecast_level = latest.value_cm + rise
        lines = [
            f"Die Strunde liegt aktuell bei {_format_level(latest.value_cm)}.",
            f"Wenn die DWD-Niederschlagsprognose eintritt, schaetze ich den Pegel in den naechsten {horizon_hours} Stunden auf etwa {_format_level(forecast_level)}.",
            rain_line,
            f"Einordnung: Das Modell sieht den staerksten Zusammenhang bei rund {model.lag_hours} Stunden Verzoegerung.",
        ]
        return "\n".join(lines)

    def rain_level_model(self) -> RainLevelModel:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=max(30, min(365, self.settings.strunde_cache_retention_days)))
        levels = self.level_cache.observations_between(start, end)
        rain = self._rain_between(start, end)
        return build_rain_level_model(levels, rain)

    def _rain_between(self, start: datetime, end: datetime) -> list[LocalObservation]:
        measurements = set(self.settings.strunde_rain_measurements) if self.settings.strunde_rain_measurements else None
        return self.weather_cache.observations_between(
            start,
            end,
            fields={LOCAL_FIELD_MAP["precipitation"]},
            measurements=measurements,
        )

    def _precipitation_forecasts(self, horizon_hours: int) -> list[ForecastPoint]:
        now = datetime.now(timezone.utc)
        try:
            forecasts = self.forecast_archive.read()
        except Exception:  # noqa: BLE001 - chat should degrade if archive is missing/corrupt.
            return []
        return [
            item
            for item in forecasts
            if item.variable == "precipitation" and now <= item.valid_at <= now + timedelta(hours=horizon_hours)
        ]


def build_rain_level_model(
    levels: list[StrundeLevelObservation],
    rain: list[LocalObservation],
) -> RainLevelModel:
    if len(levels) < 6 or len(rain) < 6:
        return RainLevelModel(0, 6, 6, 0.0, 0.0, 0.0, 0)
    best = RainLevelModel(0, 6, 6, 0.0, 0.0, 0.0, 0)
    for lag in (1, 2, 3, 6, 12, 24):
        for window in (3, 6, 12, 24):
            pairs = _rain_level_pairs(levels, rain, lag_hours=lag, rain_window_hours=window, response_hours=6)
            if len(pairs) < 6:
                continue
            model = _fit_pairs(pairs, lag, window, 6)
            if abs(model.correlation) > abs(best.correlation):
                best = model
    return best


def _rain_level_pairs(
    levels: list[StrundeLevelObservation],
    rain: list[LocalObservation],
    *,
    lag_hours: int,
    rain_window_hours: int,
    response_hours: int,
) -> list[tuple[float, float]]:
    ordered_levels = sorted(levels, key=lambda item: item.time)
    pairs: list[tuple[float, float]] = []
    for base in ordered_levels:
        future = _nearest_level(ordered_levels, base.time + timedelta(hours=response_hours))
        if future is None:
            continue
        rain_start = base.time - timedelta(hours=lag_hours + rain_window_hours)
        rain_end = base.time - timedelta(hours=lag_hours)
        rain_sum = sum(_numeric(item.value) for item in rain if rain_start <= item.time < rain_end)
        level_delta = future.value_cm - base.value_cm
        pairs.append((rain_sum, level_delta))
    return pairs


def _fit_pairs(pairs: list[tuple[float, float]], lag: int, window: int, response: int) -> RainLevelModel:
    xs = [item[0] for item in pairs]
    ys = [item[1] for item in pairs]
    x_mean = mean(xs)
    y_mean = mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    x_var = sum((x - x_mean) ** 2 for x in xs)
    y_var = sum((y - y_mean) ** 2 for y in ys)
    if x_var <= 0 or y_var <= 0:
        return RainLevelModel(lag, window, response, 0.0, 0.0, y_mean, len(pairs))
    slope = numerator / x_var
    intercept = y_mean - slope * x_mean
    correlation = numerator / sqrt(x_var * y_var)
    return RainLevelModel(lag, window, response, float(correlation), float(slope), float(intercept), len(pairs))


def _nearest_level(levels: list[StrundeLevelObservation], target: datetime) -> StrundeLevelObservation | None:
    if not levels:
        return None
    nearest = min(levels, key=lambda item: abs(item.time - target))
    if abs(nearest.time - target) > timedelta(hours=1):
        return None
    return nearest


def _missing_level_answer() -> str:
    return "\n".join(
        [
            "Ich habe noch keine gespeicherten Pegeldaten fuer die Strunde.",
            "Pegel: nicht verfuegbar.",
            "Niederschlag: kann erst mit Pegeldaten korreliert werden.",
            "Prognose: noch nicht belastbar. Sobald InfluxDB wieder erreichbar ist, kann der Strunde-Pegel synchronisiert werden.",
        ]
    )


def _model_context_line(model: RainLevelModel) -> str:
    if model.samples <= 0:
        return "Zusammenhang Regen/Pegel: noch nicht berechenbar, weil Pegel- oder Niederschlagsdaten fehlen."
    return (
        "Zusammenhang Regen/Pegel: "
        f"Korrelation {model.correlation:.2f} bei {model.lag_hours} h Verzoegerung "
        f"auf Basis von {model.samples} Vergleichspunkten."
    )


def _numeric(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _format_level(value: float) -> str:
    return f"{_format_metric(value)} cm"


def _format_metric(value: float) -> str:
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _format_datetime(value: datetime) -> str:
    return value.astimezone().strftime("%d.%m.%Y um %H:%M Uhr")


def _normalize(value: str) -> str:
    normalized = value.lower()
    for source, replacement in {
        "\u00e4": "ae",
        "\u00f6": "oe",
        "\u00fc": "ue",
        "\u00df": "ss",
    }.items():
        normalized = normalized.replace(source, replacement)
    for source, replacement in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        normalized = normalized.replace(source, replacement)
    return normalized
