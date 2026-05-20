from datetime import datetime, timezone
from io import BytesIO
import zipfile

from weather_ai.mosmix import mosmix_latest_url, parse_mosmix_kmz


def test_mosmix_latest_url_builds_single_station_latest_url():
    url = mosmix_latest_url("https://opendata.dwd.de", "G005", "MOSMIX_L")

    assert url == (
        "https://opendata.dwd.de/weather/local_forecasts/mos/MOSMIX_L/"
        "single_stations/G005/kml/MOSMIX_L_LATEST_G005.kmz"
    )


def test_parse_mosmix_kmz_extracts_forecast_points():
    kmz = _sample_kmz()

    points = parse_mosmix_kmz(kmz, "G005")

    temperatures = [item for item in points if item.variable == "temperature"]
    precipitation = [item for item in points if item.variable == "precipitation"]
    pressure = [item for item in points if item.variable == "pressure"]

    assert len(temperatures) == 2
    assert round(temperatures[0].value, 1) == 10.0
    assert temperatures[0].unit == "degC"
    assert temperatures[0].issued_at == datetime(2026, 5, 20, 3, 0, tzinfo=timezone.utc)
    assert temperatures[0].valid_at == datetime(2026, 5, 20, 4, 0, tzinfo=timezone.utc)
    assert temperatures[0].horizon_hours == 1
    assert len(precipitation) == 1
    assert precipitation[0].value == 0.4
    assert pressure[0].value == 1013.2


def _sample_kmz() -> bytes:
    kml = """<?xml version="1.0" encoding="UTF-8"?>
<kml:kml xmlns:kml="http://www.opengis.net/kml/2.2"
         xmlns:dwd="https://opendata.dwd.de/weather/lib/pointforecast_dwd_extension_V1_0.xsd">
  <kml:Document>
    <kml:ExtendedData>
      <dwd:ProductDefinition>
        <dwd:IssueTime>2026-05-20T03:00:00.000Z</dwd:IssueTime>
        <dwd:ForecastTimeSteps>
          <dwd:TimeStep>2026-05-20T04:00:00.000Z</dwd:TimeStep>
          <dwd:TimeStep>2026-05-20T05:00:00.000Z</dwd:TimeStep>
        </dwd:ForecastTimeSteps>
      </dwd:ProductDefinition>
    </kml:ExtendedData>
    <kml:Placemark>
      <kml:name>G005</kml:name>
      <kml:ExtendedData>
        <dwd:Forecast dwd:elementName="TTT"><dwd:value>283.15 284.15</dwd:value></dwd:Forecast>
        <dwd:Forecast dwd:elementName="RR1c"><dwd:value>0.40 -</dwd:value></dwd:Forecast>
        <dwd:Forecast dwd:elementName="FF"><dwd:value>3.10 3.60</dwd:value></dwd:Forecast>
        <dwd:Forecast dwd:elementName="PPPP"><dwd:value>101320 101300</dwd:value></dwd:Forecast>
      </kml:ExtendedData>
    </kml:Placemark>
  </kml:Document>
</kml:kml>
"""
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("forecast.kml", kml)
    return stream.getvalue()
