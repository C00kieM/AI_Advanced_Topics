from __future__ import annotations

from datetime import datetime, timezone

from .config import Settings
from .dwd import DwdClient
from .influx import InfluxClient, InfluxError
from .models import StatusReport
from .mosmix import MosmixClient


CANDIDATE_MEASUREMENTS = [
    "wetterdaten-gl-fw-1",
    "wetterdaten-gl-fw-2",
    "wetterdaten-gl-gronau",
    "wetterdaten-gl-mitte",
    "wetterdaten-untereschbach-neichen",
]


def build_status(settings: Settings) -> StatusReport:
    report = StatusReport()
    influx = InfluxClient(settings)
    try:
        report.local_latest = influx.latest_observations(settings.local_measurement)
        report.candidate_measurements = influx.latest_measurement_times(CANDIDATE_MEASUREMENTS)
        report.influx_ok = True
    except InfluxError as exc:
        report.warnings.append(str(exc))
    except Exception as exc:  # noqa: BLE001 - diagnostics should report all environment failures.
        report.warnings.append(f"Unexpected InfluxDB diagnostics error: {exc}")

    if report.is_local_stale:
        newest = max((item.time for item in report.local_latest), default=None)
        if newest:
            report.warnings.append(
                f"Lokale Wetterdaten sind veraltet. Neuester Wert: {newest.isoformat()}."
            )
        else:
            report.warnings.append("Keine lokalen Wetterdaten fuer das konfigurierte Measurement gefunden.")

    try:
        if settings.has_mosmix_station:
            MosmixClient(settings).fetch_forecasts()
            report.dwd_ok = True
        elif settings.has_dwd_station:
            DwdClient(settings).fetch_station_overview()
            report.dwd_ok = True
        else:
            report.warnings.append("MOSMIX_STATION_ID und DWD_STATION_ID fehlen; DWD-Livecheck uebersprungen.")
    except Exception as exc:  # noqa: BLE001
        report.warnings.append(f"DWD check failed: {exc}")

    return report


def format_status(report: StatusReport) -> str:
    lines = [
        f"Status vom {datetime.now(timezone.utc).isoformat()}",
        f"InfluxDB: {'OK' if report.influx_ok else 'FEHLER'}",
        f"DWD: {'OK' if report.dwd_ok else 'FEHLT/FEHLER'}",
        "",
        "Neueste lokale Werte:",
    ]
    for observation in sorted(report.local_latest, key=lambda item: item.field):
        lines.append(
            f"- {observation.field}: {observation.value} "
            f"({observation.time.isoformat()}, {observation.measurement})"
        )
    if report.candidate_measurements:
        lines.append("")
        lines.append("Kandidaten-Measurements:")
        for measurement, latest in report.candidate_measurements.items():
            timestamp = latest.isoformat() if latest else "keine Daten"
            lines.append(f"- {measurement}: {timestamp}")
    if report.warnings:
        lines.append("")
        lines.append("Warnungen:")
        for warning in report.warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines)
