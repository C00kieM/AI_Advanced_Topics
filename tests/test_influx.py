import pytest
from datetime import datetime, timezone

from weather_ai.config import Settings
from weather_ai.influx import (
    InfluxClient,
    InfluxWriteBlockedError,
    _parse_query_csv_payload,
    latest_observations_flux,
    strunde_level_rows_since_flux,
)


def test_latest_observations_flux_targets_measurement():
    flux = latest_observations_flux("iot", "wetterdaten-gl-fw-1", 365)
    assert 'from(bucket: "iot")' in flux
    assert 'r["_measurement"] == "wetterdaten-gl-fw-1"' in flux
    assert "|> last()" in flux


def test_strunde_level_flux_targets_configured_measurement_and_field():
    flux = strunde_level_rows_since_flux(
        'iot"prod',
        'pegel-strunde"',
        "water\\level",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert 'from(bucket: "iot\\"prod")' in flux
    assert 'r["_measurement"] == "pegel-strunde\\""' in flux
    assert 'r["_field"] == "water\\\\level"' in flux
    assert 'range(start: time(v: "2026-07-01T00:00:00Z"))' in flux


def test_query_csv_parser_handles_influx_annotated_csv():
    payload = """#group,false,false,true,true,false,false,true,true
#datatype,string,long,dateTime:RFC3339,double,string,string
#default,_result,,,,,
,result,table,_time,_value,_field,_measurement
,,0,2026-07-06T11:00:02Z,25.8,pegelstandgronau,strundepegel-gronau
,,0,2026-07-06T11:09:54Z,25.7,pegelstandgronau,strundepegel-gronau
"""

    rows = _parse_query_csv_payload(payload)

    assert len(rows) == 2
    assert rows[0]["_time"] == "2026-07-06T11:00:02Z"
    assert rows[0]["_field"] == "pegelstandgronau"
    assert rows[0]["_measurement"] == "strundepegel-gronau"


def test_influx_write_forecasts_is_blacklisted():
    client = InfluxClient(Settings.from_env())

    with pytest.raises(InfluxWriteBlockedError, match="blacklisted"):
        client.write_forecasts([])


def test_influx_write_endpoint_is_blacklisted_even_for_raw_requests():
    client = InfluxClient(Settings.from_env())
    request = __import__("urllib.request").request.Request(
        "http://influx.example/api/v2/write?org=test",
        data=b"measurement value=1",
        method="POST",
    )

    with pytest.raises(InfluxWriteBlockedError, match="blacklisted"):
        client._open(request, timeout=1)
