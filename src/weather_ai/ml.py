from __future__ import annotations

from dataclasses import dataclass, field
from math import cos, pi, sin, sqrt
from pathlib import Path
from statistics import mean
from typing import Any

from .models import ComparisonPoint, ForecastPoint


@dataclass
class TrainingResult:
    trained: bool
    message: str
    metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    model_paths: dict[str, str] = field(default_factory=dict)


FEATURE_NAMES = [
    "forecast_value",
    "horizon_hours",
    "valid_hour_sin",
    "valid_hour_cos",
    "valid_day_sin",
    "valid_day_cos",
]


@dataclass
class ForecastCorrectionModel:
    variable: str
    estimator: Any
    feature_names: list[str]
    non_negative: bool = False

    def predict(self, rows):
        features = [_normalize_feature_row(row) for row in rows]
        predictions = self.estimator.predict(features)
        if self.non_negative:
            return [max(0.0, float(value)) for value in predictions]
        return predictions


def train_models(comparisons: list[ComparisonPoint], model_dir: Path, min_points: int = 24) -> TrainingResult:
    grouped: dict[str, list[ComparisonPoint]] = {}
    for item in comparisons:
        grouped.setdefault(item.variable, []).append(item)

    metrics: dict[str, dict[str, float]] = {}
    model_paths: dict[str, str] = {}
    any_trained = False

    for variable, items in grouped.items():
        if len(items) < min_points:
            metrics[variable] = {"points": float(len(items))}
            continue
        result = _train_variable(variable, items, model_dir)
        metrics[variable] = result["metrics"]
        if result.get("path"):
            model_paths[variable] = result["path"]
            any_trained = True

    if any_trained:
        return TrainingResult(True, "Modelle wurden trainiert und gegen DWD-Rohwerte bewertet.", metrics, model_paths)
    return TrainingResult(False, "Nicht genug Forecast-vs-Ist-Paare fuer belastbares Training.", metrics, model_paths)


def _train_variable(variable: str, items: list[ComparisonPoint], model_dir: Path) -> dict[str, Any]:
    try:
        from joblib import dump
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.linear_model import LinearRegression
        from sklearn.metrics import mean_absolute_error, mean_squared_error
        from sklearn.model_selection import train_test_split
    except Exception as exc:  # noqa: BLE001
        baseline_mae = mean(abs(item.error) for item in items)
        return {
            "metrics": {
                "points": float(len(items)),
                "baseline_mae": baseline_mae,
                "error": float("nan"),
            },
            "error": f"scikit-learn/joblib unavailable: {exc}",
        }

    ordered = sorted(items, key=lambda item: item.forecast_valid_at)
    x = [features_for_comparison(item) for item in ordered]
    y = [item.actual_value for item in ordered]
    if len(items) < 40:
        x_train, x_test, y_train, y_test = _chronological_holdout(x, y)
        validation = "chronological_holdout"
    else:
        x_train, x_test, y_train, y_test = train_test_split(x, y, test_size=0.25, random_state=42)
        validation = "random_holdout"

    baseline_predictions = [row[0] for row in x_test]
    baseline_mae = mean_absolute_error(y_test, baseline_predictions)
    baseline_rmse = _rmse(y_test, baseline_predictions, mean_squared_error)
    baseline_bias = float(mean([actual - forecast for actual, forecast in zip(y_test, baseline_predictions)]))

    candidates = {
        "linear": LinearRegression(),
        "random_forest": RandomForestRegressor(
            n_estimators=180,
            random_state=42,
            min_samples_leaf=max(2, len(x_train) // 24),
        ),
    }
    best_name = ""
    best_model = None
    best_mae = float("inf")
    best_rmse = float("inf")
    best_bias = float("nan")
    for name, model in candidates.items():
        model.fit(x_train, y_train)
        predictions = model.predict(x_test)
        if variable == "precipitation":
            predictions = [max(0.0, float(value)) for value in predictions]
        mae = mean_absolute_error(y_test, predictions)
        rmse = _rmse(y_test, predictions, mean_squared_error)
        if mae < best_mae:
            best_name = name
            best_model = model
            best_mae = mae
            best_rmse = rmse
            best_bias = float(mean([actual - predicted for actual, predicted in zip(y_test, predictions)]))

    mae_improvement = float(baseline_mae - best_mae)
    improved = mae_improvement > 0
    metrics = {
        "points": float(len(items)),
        "train_points": float(len(x_train)),
        "test_points": float(len(x_test)),
        "baseline_mae": float(baseline_mae),
        "baseline_rmse": float(baseline_rmse),
        "baseline_bias": baseline_bias,
        "model_mae": float(best_mae),
        "model_rmse": float(best_rmse),
        "model_bias": best_bias,
        "mae_improvement": mae_improvement,
        "rmse_improvement": float(baseline_rmse - best_rmse),
        "skill_score_mae": _skill_score(baseline_mae, best_mae),
        "validation": validation,
        "model": best_name,
        "improved": float(1 if improved else 0),
    }
    if not improved:
        return {"metrics": metrics}

    model_dir.mkdir(parents=True, exist_ok=True)
    path = model_dir / f"{variable}-{best_name}.joblib"
    dump(
        ForecastCorrectionModel(
            variable=variable,
            estimator=best_model,
            feature_names=FEATURE_NAMES,
            non_negative=variable == "precipitation",
        ),
        path,
    )
    return {
        "path": str(path),
        "metrics": metrics,
    }


def features_for_comparison(item: ComparisonPoint) -> list[float]:
    return _features(item.forecast_value, item.horizon_hours, item.forecast_valid_at)


def features_for_forecast(item: ForecastPoint) -> list[float]:
    return _features(item.value, item.horizon_hours, item.valid_at)


def _features(value: float, horizon_hours: float, valid_at) -> list[float]:
    hour_angle = 2 * pi * ((valid_at.hour + valid_at.minute / 60) / 24)
    day_angle = 2 * pi * (valid_at.timetuple().tm_yday / 366)
    return [
        float(value),
        float(horizon_hours),
        sin(hour_angle),
        cos(hour_angle),
        sin(day_angle),
        cos(day_angle),
    ]


def _normalize_feature_row(row) -> list[float]:
    values = [float(value) for value in row]
    if len(values) == len(FEATURE_NAMES):
        return values
    if len(values) == 2:
        return [values[0], values[1], 0.0, 1.0, 0.0, 1.0]
    raise ValueError(f"Expected 2 or {len(FEATURE_NAMES)} model features, got {len(values)}.")


def _chronological_holdout(x: list[list[float]], y: list[float]):
    test_size = min(len(x) - 12, max(6, round(len(x) * 0.25)))
    test_size = max(1, test_size)
    split = len(x) - test_size
    return x[:split], x[split:], y[:split], y[split:]


def _skill_score(baseline_mae: float, model_mae: float) -> float:
    if baseline_mae <= 0:
        return 0.0
    return float((baseline_mae - model_mae) / baseline_mae)


def _rmse(y_true, y_pred, mean_squared_error_func) -> float:
    return sqrt(float(mean_squared_error_func(y_true, y_pred)))
