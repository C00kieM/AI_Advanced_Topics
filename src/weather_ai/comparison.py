from __future__ import annotations

from datetime import timedelta

from .models import ComparisonPoint, ForecastPoint, LocalObservation, MODEL_VARIABLES


def match_forecasts_to_observations(
    forecasts: list[ForecastPoint],
    observations: list[LocalObservation],
    tolerance: timedelta = timedelta(minutes=45),
) -> list[ComparisonPoint]:
    numeric_observations = [
        item for item in observations if item.variable in MODEL_VARIABLES and isinstance(item.value, (int, float))
    ]
    comparisons: list[ComparisonPoint] = []
    for forecast in forecasts:
        if forecast.variable not in MODEL_VARIABLES:
            continue
        candidates = [
            item
            for item in numeric_observations
            if item.variable == forecast.variable and abs(item.time - forecast.valid_at) <= tolerance
        ]
        if not candidates:
            continue
        nearest = min(candidates, key=lambda item: abs(item.time - forecast.valid_at))
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
