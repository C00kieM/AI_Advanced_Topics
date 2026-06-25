from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt
from pathlib import Path
from statistics import mean
from typing import Any

from .models import ComparisonPoint


@dataclass
class TrainingResult:
    trained: bool
    message: str
    metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    model_paths: dict[str, str] = field(default_factory=dict)


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

    x = [[item.forecast_value, item.horizon_hours] for item in items]
    y = [item.actual_value for item in items]
    if len(items) < 40:
        x_train, x_test, y_train, y_test = x, x, y, y
    else:
        x_train, x_test, y_train, y_test = train_test_split(x, y, test_size=0.25, random_state=42)

    baseline_predictions = [row[0] for row in x_test]
    baseline_mae = mean_absolute_error(y_test, baseline_predictions)
    baseline_rmse = _rmse(y_test, baseline_predictions, mean_squared_error)

    candidates = {
        "linear": LinearRegression(),
        "random_forest": RandomForestRegressor(n_estimators=100, random_state=42, min_samples_leaf=3),
    }
    best_name = ""
    best_model = None
    best_mae = float("inf")
    best_rmse = float("inf")
    for name, model in candidates.items():
        model.fit(x_train, y_train)
        predictions = model.predict(x_test)
        mae = mean_absolute_error(y_test, predictions)
        rmse = _rmse(y_test, predictions, mean_squared_error)
        if mae < best_mae:
            best_name = name
            best_model = model
            best_mae = mae
            best_rmse = rmse

    model_dir.mkdir(parents=True, exist_ok=True)
    path = model_dir / f"{variable}-{best_name}.joblib"
    dump(best_model, path)
    return {
        "path": str(path),
        "metrics": {
            "points": float(len(items)),
            "baseline_mae": float(baseline_mae),
            "baseline_rmse": float(baseline_rmse),
            "model_mae": float(best_mae),
            "model_rmse": float(best_rmse),
            "mae_improvement": float(baseline_mae - best_mae),
        },
    }


def _rmse(y_true, y_pred, mean_squared_error_func) -> float:
    return sqrt(float(mean_squared_error_func(y_true, y_pred)))
