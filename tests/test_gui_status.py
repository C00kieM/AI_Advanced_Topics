from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

from weather_ai.config import Settings
from weather_ai.gui_status import _observation_days_for_forecasts, build_gui_status, summarize_cached_comparison, summarize_dwd_data, summarize_local_cache, summarize_strunde_cache
from weather_ai.models import ForecastPoint


def test_gui_comparison_lookback_covers_oldest_forecast():
    settings = replace(Settings.from_env(), local_cache_retention_days=1095)
    forecast = ForecastPoint(
        source="dwd-opendata-mosmix",
        station_id="10513",
        variable="temperature",
        value=20.0,
        issued_at=datetime.now(timezone.utc) - timedelta(days=42, hours=1),
        valid_at=datetime.now(timezone.utc) - timedelta(days=42),
        horizon_hours=1.0,
    )

    assert _observation_days_for_forecasts(settings, [forecast]) >= 43


def test_gui_status_uses_saved_comparison_summary_without_recomputing():
    model_dir = Path(".test-runtime") / "saved-comparison-models"
    summary_path = model_dir / "global" / "comparison_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-07-07T10:00:00+00:00",
                "scope": {"kind": "all"},
                "pairs": 12,
                "summary": {"temperature": {"count": 12, "mae": 1.2, "bias": 0.2}},
            }
        ),
        encoding="utf-8",
    )
    settings = replace(Settings.from_env(), model_dir=model_dir)

    payload = summarize_cached_comparison(settings, forecasts=[])

    assert payload["saved"] is True
    assert payload["pairs"] == 12
    assert payload["counts"] == {"temperature": 12}


def test_gui_status_offline_mode_skips_live_check():
    settings = replace(Settings.from_env(), offline_mode=True)

    payload = build_gui_status(settings, live=True, deep=False)

    assert payload["live"]["checked"] is False
    assert "Offline-Modus aktiv" in payload["live"]["warnings"][0]


def test_local_cache_status_uses_selected_measurement_freshness():
    path = Path("tests/fixtures/local_cache_selected_stale.csv")

    summary = summarize_local_cache(path, selected_measurement="target")

    assert summary["selected_rows"] == 1
    assert summary["stale"] is True


def test_dwd_status_exposes_observation_and_forecast_time_ranges():
    path = Path("tests/fixtures/dwd_status_ranges.csv")

    summary, forecasts = summarize_dwd_data(path)

    assert summary["min_time"] == "2024-01-01T00:00:00+00:00"
    assert summary["max_time"] == "2026-06-01T00:00:00+00:00"
    assert summary["observation_min_time"] == "2024-01-01T00:00:00+00:00"
    assert summary["observation_max_time"] == "2026-01-01T00:00:00+00:00"
    assert summary["min_valid_at"] == "2026-06-02T06:00:00+00:00"
    assert summary["max_valid_at"] == "2026-06-02T12:00:00+00:00"
    assert summary["forecast_rows"] == 2
    assert len(forecasts) == 2


def test_strunde_status_summarizes_level_cache():
    summary = summarize_strunde_cache(Path("tests/fixtures/strunde_water_level.csv"))

    assert summary["rows"] == 3
    assert summary["min_time"] == "2026-06-30T08:00:00+00:00"
    assert summary["max_time"] == "2026-07-01T08:00:00+00:00"
    assert summary["latest_level_cm"] == 43.0
    assert summary["min_level_cm"] == 41.0
    assert summary["max_level_cm"] == 45.0


def test_strunde_status_filters_measurement_field_and_invalid_levels():
    path = Path(".test-runtime") / "strunde-status-filter.csv"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "time,measurement,field,value",
                "2026-07-01T08:00:00+00:00,old,old_field,999",
                "2026-07-01T09:00:00+00:00,strundepegel-gronau,pegelstandgronau,-611",
                "2026-07-01T10:00:00+00:00,strundepegel-gronau,pegelstandgronau,42",
            ]
        ),
        encoding="utf-8",
    )

    summary = summarize_strunde_cache(path, measurement="strundepegel-gronau", field="pegelstandgronau")

    assert summary["rows"] == 2
    assert summary["latest_level_cm"] == 42.0
    assert summary["min_level_cm"] == 42.0
    assert summary["max_level_cm"] == 42.0
    try:
        path.unlink()
    except PermissionError:
        pass
