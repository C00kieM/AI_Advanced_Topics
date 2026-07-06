from __future__ import annotations

from dataclasses import dataclass
import re

from .local_cache import read_cache_rows
from .config import Settings


@dataclass(frozen=True)
class StationInfo:
    measurement: str
    label: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class StationScope:
    kind: str
    measurements: set[str] | None
    label: str
    station: StationInfo | None = None
    matches: tuple[StationInfo, ...] = ()

    @property
    def is_ambiguous(self) -> bool:
        return self.kind == "ambiguous"

    @property
    def is_single(self) -> bool:
        return self.kind == "single"


def station_scope_from_question(settings: Settings, question: str) -> StationScope:
    stations = station_catalog(settings)
    if not stations:
        return StationScope(kind="all", measurements=None, label="alle Stationen")
    normalized = normalize_station_text(question)

    exact = [station for station in stations if normalize_station_text(station.measurement) in normalized]
    if len(exact) == 1:
        return _single_scope(exact[0])
    if len(exact) > 1:
        return _ambiguous_scope(exact)

    wanted = _station_alias_terms(normalized)
    if not wanted:
        return StationScope(kind="all", measurements=None, label="alle Stationen")

    matches: list[StationInfo] = []
    for term in wanted:
        term_matches = [
            station
            for station in stations
            if term in {normalize_station_text(alias) for alias in station.aliases}
        ]
        matches.extend(term_matches)
    unique = _unique_stations(matches)
    if len(unique) == 1:
        return _single_scope(unique[0])
    if len(unique) > 1:
        return _ambiguous_scope(unique)
    return StationScope(kind="all", measurements=None, label="alle Stationen")


def station_catalog(settings: Settings) -> list[StationInfo]:
    measurements = sorted({row["measurement"] for row in read_cache_rows(settings.local_cache_path) if row.get("measurement")})
    return [StationInfo(measurement=item, label=_station_label(item), aliases=tuple(_station_aliases(item))) for item in measurements]


def scoped_model_dir(settings: Settings, scope: StationScope | None = None):
    scope = scope or StationScope(kind="all", measurements=None, label="alle Stationen")
    if scope.is_single and scope.station is not None:
        return settings.model_dir / "stations" / _slug(scope.station.measurement)
    return settings.model_dir / "global"


def normalize_station_text(value: str) -> str:
    normalized = value.strip().lower()
    for source, replacement in {
        "\u00e4": "ae",
        "\u00f6": "oe",
        "\u00fc": "ue",
        "\u00df": "ss",
    }.items():
        normalized = normalized.replace(source, replacement)
    replacements = (
        ("ä", "ae"),
        ("ö", "oe"),
        ("ü", "ue"),
        ("ß", "ss"),
        ("_", "-"),
    )
    for source, replacement in replacements:
        normalized = normalized.replace(source, replacement)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _single_scope(station: StationInfo) -> StationScope:
    return StationScope(kind="single", measurements={station.measurement}, label=station.label, station=station)


def _ambiguous_scope(stations: list[StationInfo]) -> StationScope:
    unique = tuple(_unique_stations(stations))
    return StationScope(kind="ambiguous", measurements=None, label="mehrdeutige Station", matches=unique)


def _station_alias_terms(normalized_question: str) -> list[str]:
    terms: list[str] = []
    for match in re.finditer(r"\b(?:station|wetterstation)\s+([a-z0-9][a-z0-9 -]{0,32})", normalized_question):
        terms.append(_clean_station_term(match.group(1)))
    for match in re.finditer(r"\b(?:bei|an)\s+(?:der\s+|die\s+|dem\s+)?(?:station\s+|wetterstation\s+)?([a-z0-9][a-z0-9-]{0,24})", normalized_question):
        terms.append(_clean_station_term(match.group(1)))
    return [term for term in terms if term]


def _clean_station_term(value: str) -> str:
    value = value.strip(" .,!?:;")
    value = re.split(r"\b(?:morgen|gestern|heute|und|wetter|wie|warm|kalt)\b", value, maxsplit=1)[0]
    return normalize_station_text(value.strip(" .,!?:;"))


def _station_label(measurement: str) -> str:
    parts = [part for part in normalize_station_text(measurement).split("-") if part]
    if parts[:2] == ["wetterdaten", "gl"]:
        parts = parts[2:]
    elif parts[:1] == ["wetterdaten"]:
        parts = parts[1:]
    if not parts:
        return measurement
    return "-".join(parts).upper() if parts[0] == "fw" else " ".join(part.capitalize() for part in parts)


def _station_aliases(measurement: str) -> list[str]:
    normalized = normalize_station_text(measurement)
    parts = [part for part in normalized.split("-") if part]
    aliases = {normalized, normalized.replace("-", " ")}
    if parts and parts[0] == "wetterdaten":
        suffix = parts[1:]
        aliases.add("-".join(suffix))
        aliases.add(" ".join(suffix))
        if suffix and suffix[0] == "gl":
            short = suffix[1:]
            aliases.add("-".join(short))
            aliases.add(" ".join(short))
            if short and short[0] == "fw" and len(short) > 1:
                aliases.add(f"fw-{short[1]}")
                aliases.add(f"fw {short[1]}")
                aliases.add(f"fw{short[1]}")
            if short:
                aliases.add(short[-1])
    return sorted(alias for alias in aliases if alias)


def _unique_stations(stations: list[StationInfo]) -> list[StationInfo]:
    seen: set[str] = set()
    unique: list[StationInfo] = []
    for station in stations:
        if station.measurement in seen:
            continue
        seen.add(station.measurement)
        unique.append(station)
    return unique


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", normalize_station_text(value)).strip("-")
