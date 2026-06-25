from datetime import datetime, timezone
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import zipfile

from weather_ai.config import Settings
from weather_ai.dwd_historical import (
    DwdHistoricalCsvStore,
    DwdHistoricalError,
    cdc_directory_url,
    merge_historical_rows,
    parse_cdc_zip,
    zip_links_for_station,
)


def test_cdc_directory_url_builds_parameter_period_path():
    assert cdc_directory_url("http://example.test/CDC", "10_minutes", "air_temperature", "historical") == (
        "http://example.test/CDC/observations_germany/climate/10_minutes/air_temperature/historical/"
    )


def test_zip_links_for_station_finds_matching_archives():
    html = """
    <a href="10minutenwerte_TU_02667_20200101_20251231_hist.zip">file</a>
    <a href="10minutenwerte_TU_02968_20200101_20251231_hist.zip">file</a>
    """

    assert zip_links_for_station(html, "02667") == ["10minutenwerte_TU_02667_20200101_20251231_hist.zip"]


def test_parse_cdc_zip_returns_long_records():
    records = parse_cdc_zip(_sample_zip(), "air_temperature", "02667", "http://example.test/file.zip")

    assert len(records) == 3
    temperature = [item for item in records if item.field == "TT_10"][0]
    assert temperature.time == datetime(2026, 5, 20, 9, 20, tzinfo=timezone.utc)
    assert temperature.station_id == "02667"
    assert temperature.dataset == "air_temperature"
    assert temperature.value == 12.6
    assert temperature.quality == "3"


def test_merge_historical_rows_deduplicates_by_station_dataset_field_time():
    cutoff = datetime(2023, 1, 1, tzinfo=timezone.utc)
    existing = [
        {
            "time": "2026-05-20T09:20:00+00:00",
            "station_id": "02667",
            "dataset": "air_temperature",
            "field": "TT_10",
            "value": "1",
            "quality": "3",
            "source_url": "old",
        }
    ]
    fetched = [{**existing[0], "value": "2", "source_url": "new"}]

    merged = merge_historical_rows(existing, fetched, cutoff)

    assert len(merged) == 1
    assert merged[0]["value"] == "2"
    assert merged[0]["source_url"] == "new"


def test_dwd_historical_sync_skips_network_when_already_attempted_today():
    settings = replace(Settings.from_env(), dwd_data_path=Path("unused-dwd.csv"))
    existing = [
        {
            "kind": "observation",
            "time": datetime.now(timezone.utc).isoformat(),
            "station_id": "02667",
            "dataset": "air_temperature",
            "field": "TT_10",
            "value": "2",
        }
    ]

    with (
        patch("weather_ai.dwd_historical._has_existing_cache", return_value=True),
        patch("weather_ai.dwd_historical.read_dwd_rows", return_value=existing),
        patch(
            "weather_ai.dwd_historical.read_sync_state",
            return_value={"attempted_date": datetime.now(timezone.utc).date().isoformat()},
        ),
        patch("weather_ai.dwd_historical.DwdHistoricalClient.fetch_records") as fetch_records,
    ):
        result = DwdHistoricalCsvStore(settings).sync()

    assert result.skipped is True
    assert result.fetched_records == 0
    assert "Heute wurde bereits synchronisiert" in (result.reason or "")
    fetch_records.assert_not_called()


def test_dwd_historical_sync_keeps_existing_history_when_dwd_is_unreachable():
    settings = replace(Settings.from_env(), dwd_data_path=Path("unused-dwd.csv"))
    existing = [
        {
            "kind": "observation",
            "time": datetime.now(timezone.utc).isoformat(),
            "station_id": "02667",
            "dataset": "air_temperature",
            "field": "TT_10",
            "value": "2",
        }
    ]

    with (
        patch("weather_ai.dwd_historical.read_dwd_rows", return_value=existing),
        patch("weather_ai.dwd_historical.write_dwd_rows") as write_rows,
        patch("weather_ai.dwd_historical.write_sync_state"),
        patch(
            "weather_ai.dwd_historical.DwdHistoricalClient.fetch_records",
            side_effect=DwdHistoricalError("timeout"),
        ),
    ):
        result = DwdHistoricalCsvStore(settings).sync()

    assert result.fetched_records == 0
    assert result.written_rows == 1
    assert result.warning is not None
    assert "vorhandene 3-Jahres-Historie bleibt aktiv" in result.warning
    write_rows.assert_called_once()


def _sample_zip() -> bytes:
    text = "\n".join(
        [
            "STATIONS_ID;MESS_DATUM;QN;PP_10;TT_10;eor",
            "02667;202605200920;3;977.9;12.6;eor",
            "02667;202605200930;3;-999;12.8;eor",
        ]
    )
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("produkt_zehn_min_tu_02667_20200101_20251231_hist.txt", text)
    return stream.getvalue()
