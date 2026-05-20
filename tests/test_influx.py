import pytest

from weather_ai.config import Settings
from weather_ai.influx import InfluxClient, InfluxWriteBlockedError, latest_observations_flux


def test_latest_observations_flux_targets_measurement():
    flux = latest_observations_flux("iot", "wetterdaten-gl-fw-1", 365)
    assert 'from(bucket: "iot")' in flux
    assert 'r["_measurement"] == "wetterdaten-gl-fw-1"' in flux
    assert "|> last()" in flux


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
