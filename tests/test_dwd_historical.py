from datetime import datetime, timezone
from io import BytesIO
import zipfile

from weather_ai.dwd_historical import (
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
