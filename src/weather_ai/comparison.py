from __future__ import annotations

from bisect import bisect_left
from datetime import timedelta

from .models import ComparisonPoint, ForecastPoint, LocalObservation, MODEL_VARIABLES


def match_forecasts_to_observations(
    forecasts: list[ForecastPoint],
    observations: list[LocalObservation],
    tolerance: timedelta = timedelta(minutes=45),
) -> list[ComparisonPoint]:
    grouped_observations: dict[str, list[LocalObservation]] = {}
    for item in observations:
        if item.variable in MODEL_VARIABLES and isinstance(item.value, (int, float)):
            grouped_observations.setdefault(item.variable, []).append(item)
    for items in grouped_observations.values():
        items.sort(key=lambda observation: observation.time)
    grouped_times = {
        variable: [observation.time for observation in items]
        for variable, items in grouped_observations.items()
    }

    comparisons: list[ComparisonPoint] = []
    for forecast in forecasts:
        if forecast.variable not in MODEL_VARIABLES:
            continue
        variable_observations = grouped_observations.get(forecast.variable, [])
        variable_times = grouped_times.get(forecast.variable, [])
        if not variable_observations:
            continue
        nearest = _nearest_observation(forecast.valid_at, variable_observations, variable_times, tolerance)
        if nearest is None:
            continue
        actual_value = float(nearest.value)
        comparisons.append(
            ComparisonPoint(
                variable=forecast.variable,
                forecast_value=forecast.value,
                actual_value=actual_value,
                error=actual_value - forecast.value,
                forecast_valid_at=forecast.valid_at,
                observation_time=nearest.time,
                horizon_hours=forecast.horizon_hours,
            )
        )
    return comparisons


def _nearest_observation(
    target,
    observations: list[LocalObservation],
    times,
    tolerance: timedelta,
) -> LocalObservation | None:
    index = bisect_left(times, target)
    candidates: list[LocalObservation] = []
    if index < len(observations):
        candidates.append(observations[index])
    if index > 0:
        candidates.append(observations[index - 1])
    if not candidates:
        return None
    nearest = min(candidates, key=lambda item: abs(item.time - target))
    if abs(nearest.time - target) > tolerance:
        return None
    return nearest


def summarize_comparisons(comparisons: list[ComparisonPoint]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for variable in sorted({item.variable for item in comparisons}):
        items = [item for item in comparisons if item.variable == variable]
        abs_errors = [abs(item.error) for item in items]
        signed_errors = [item.error for item in items]
        summary[variable] = {
            "count": float(len(items)),
            "mae": sum(abs_errors) / len(abs_errors) if abs_errors else 0.0,
            "bias": sum(signed_errors) / len(signed_errors) if signed_errors else 0.0,
        }
    return summary
