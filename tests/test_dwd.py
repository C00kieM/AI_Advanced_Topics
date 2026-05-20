from datetime import datetime, timezone

from weather_ai.dwd import parse_station_overview


def test_parse_station_overview_handles_warnwetter_like_forecast():
    payload = {
        "G005": {
            "forecast1": {
                "start": 1_768_521_600_000,
                "timeStep": 3_600_000,
                "temperature": [122, 131],
                "precipitationTotal": [0, 1.2],
                "windSpeed": [4.0, 5.0],
            }
        }
    }

    points = parse_station_overview(payload, "G005")

    temperatures = [item for item in points if item.variable == "temperature"]
    assert len(temperatures) == 2
    assert temperatures[0].value == 12.2
    assert temperatures[0].valid_at == datetime(2026, 1, 16, 0, 0, tzinfo=timezone.utc)
    assert any(item.variable == "precipitation" for item in points)
    assert any(item.variable == "wind_speed" for item in points)
