from datetime import datetime, timezone

from weather_ai.local_cache import latest_row_time, merge_rows, normalize_influx_rows, prune_rows


def test_normalize_influx_rows_maps_weather_values():
    rows = normalize_influx_rows(
        [
            {
                "_time": "2026-05-20T09:24:56Z",
                "_measurement": "wetterdaten-gl-fw-2",
                "_field": "Lufttemperatur",
                "_value": "12.6",
            }
        ]
    )

    assert rows == [
        {
            "time": "2026-05-20T09:24:56+00:00",
            "measurement": "wetterdaten-gl-fw-2",
            "field": "Lufttemperatur",
            "value": "12.6",
        }
    ]


def test_merge_rows_prunes_old_rows_and_deduplicates_newer_values():
    cutoff = datetime(2023, 5, 20, tzinfo=timezone.utc)
    existing = [
        {"time": "2022-01-01T00:00:00+00:00", "measurement": "wetterdaten-a", "field": "x", "value": "old"},
        {"time": "2026-05-20T09:00:00+00:00", "measurement": "wetterdaten-a", "field": "x", "value": "1"},
    ]
    fetched = [
        {
            "_time": "2026-05-20T09:00:00Z",
            "_measurement": "wetterdaten-a",
            "_field": "x",
            "_value": "2",
        },
        {
            "_time": "2026-05-20T09:15:00Z",
            "_measurement": "wetterdaten-b",
            "_field": "y",
            "_value": "3",
        },
    ]

    merged = merge_rows(existing, fetched, cutoff)

    assert len(merged) == 2
    assert merged[0]["value"] == "2"
    assert latest_row_time(merged) == datetime(2026, 5, 20, 9, 15, tzinfo=timezone.utc)
    assert prune_rows(existing, cutoff) == [existing[1]]
