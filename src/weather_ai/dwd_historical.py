from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import csv
import re
import urllib.error
import urllib.request
import zipfile

from .config import Settings
from .dwd_data import DWD_DATA_COLUMNS, merge_dwd_rows, read_dwd_rows, write_dwd_rows
from .sync_state import attempted_today, read_sync_state, sync_state_path, write_sync_state


HISTORICAL_COLUMNS = DWD_DATA_COLUMNS
DEFAULT_PARAMETERS = ("air_temperature", "precipitation", "wind")
DEFAULT_PERIODS = ("historical", "recent")
SKIP_COLUMNS = {"STATIONS_ID", "MESS_DATUM", "MESS_DATUM_BEGINN", "MESS_DATUM_ENDE", "QN", "QN_3", "QN_4", "eor"}
MIN_RETENTION_DAYS = 365 * 3


class DwdHistoricalError(RuntimeError):
    pass


@dataclass(frozen=True)
class DwdHistoricalRecord:
    time: datetime
    station_id: str
    dataset: str
    field: str
    value: float
    quality: str
    source_url: str

    def to_row(self) -> dict[str, str]:
        return {
            "kind": "observation",
            "time": self.time.isoformat(),
            "station_id": self.station_id,
            "dataset": self.dataset,
            "field": self.field,
            "value": f"{self.value:g}",
            "quality": self.quality,
            "source_url": self.source_url,
            "source": "dwd-cdc",
            "issued_at": "",
            "valid_at": "",
            "horizon_hours": "",
            "unit": "",
            "raw_name": self.field,
        }


@dataclass(frozen=True)
class DwdHistoricalSyncResult:
    path: Path
    station_ids: list[str]
    fetched_records: int
    written_rows: int
    cutoff: datetime
    skipped: bool = False
    warning: str | None = None
    reason: str | None = None


class DwdHistoricalClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def fetch_records(self, cutoff: datetime | None = None, periods: tuple[str, ...] = DEFAULT_PERIODS) -> list[DwdHistoricalRecord]:
        station_ids = self.settings.dwd_historical_station_ids
        if not station_ids:
            raise DwdHistoricalError("DWD_HISTORICAL_STATION_IDS is empty.")
        cutoff = cutoff or datetime.now(timezone.utc) - timedelta(days=self.settings.dwd_historical_retention_days)
        records: list[DwdHistoricalRecord] = []
        for parameter in self.settings.dwd_historical_parameters:
            for period in periods:
                for station_id in station_ids:
                    for url in self.find_station_archives(parameter, period, station_id):
                        records.extend(
                            record
                            for record in parse_cdc_zip(
                                payload=self._download(url),
                                dataset=parameter,
                                station_id=station_id,
                                source_url=url,
                            )
                            if record.time >= cutoff
                        )
        return records

    def find_station_archives(self, parameter: str, period: str, station_id: str) -> list[str]:
        url = cdc_directory_url(self.settings.dwd_cdc_base_url, self.settings.dwd_historical_resolution, parameter, period)
        html = self._download(url).decode("utf-8", errors="replace")
        return [
            urllib.request.urljoin(url, href)
            for href in zip_links_for_station(html, station_id)
        ]

    def _download(self, url: str) -> bytes:
        request = urllib.request.Request(url=url, method="GET", headers={"User-Agent": "weather-ai/0.1"})
        try:
            with self._opener.open(request, timeout=120) as response:
                return response.read()
        except urllib.error.URLError as exc:
            raise DwdHistoricalError(f"DWD CDC request failed for {url}: {exc}") from exc


class DwdHistoricalCsvStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.dwd_data_path
        self.state_path = sync_state_path(self.path, "dwd-history")

    def sync(self, force: bool = False) -> DwdHistoricalSyncResult:
        started_at = datetime.now(timezone.utc)
        cutoff = started_at - timedelta(days=_retention_days(self.settings.dwd_historical_retention_days))
        state = read_sync_state(self.state_path)
        if not force and _has_existing_cache(self.path) and attempted_today(state, started_at):
            written_rows = _state_count(state)
            return DwdHistoricalSyncResult(
                path=self.path,
                station_ids=self.settings.dwd_historical_station_ids,
                fetched_records=0,
                written_rows=written_rows,
                cutoff=cutoff,
                skipped=True,
                reason="DWD-CDC-Update uebersprungen: Heute wurde bereits synchronisiert.",
            )
        existing = read_dwd_rows(self.path)
        existing_observations = [row for row in existing if row.get("kind") == "observation"]
        preserved_rows = [row for row in existing if row.get("kind") != "observation"]
        pruned = [row for row in existing_observations if _row_time(row) and _row_time(row) >= cutoff]
        periods = ("recent",) if pruned else DEFAULT_PERIODS
        try:
            fetched = [record.to_row() for record in DwdHistoricalClient(self.settings).fetch_records(cutoff=cutoff, periods=periods)]
        except DwdHistoricalError as exc:
            if not pruned:
                raise
            merged = merge_dwd_rows([*preserved_rows, *pruned])
            write_dwd_rows(self.path, merged)
            write_sync_state(
                self.state_path,
                attempted_at=started_at,
                status="warning",
                details={"error": str(exc), "written_rows": len(pruned)},
            )
            return DwdHistoricalSyncResult(
                path=self.path,
                station_ids=self.settings.dwd_historical_station_ids,
                fetched_records=0,
                written_rows=len(pruned),
                cutoff=cutoff,
                warning=f"DWD-CDC ist nicht erreichbar; vorhandene 3-Jahres-Historie bleibt aktiv. Ursache: {exc}",
            )
        merged_observations = merge_historical_rows(pruned, fetched, cutoff)
        merged = merge_dwd_rows([*preserved_rows, *merged_observations])
        write_dwd_rows(self.path, merged)
        write_sync_state(
            self.state_path,
            attempted_at=started_at,
            status="success",
            details={"fetched_records": len(fetched), "written_rows": len(merged_observations)},
        )
        return DwdHistoricalSyncResult(
            path=self.path,
            station_ids=self.settings.dwd_historical_station_ids,
            fetched_records=len(fetched),
            written_rows=len(merged_observations),
            cutoff=cutoff,
        )


def _retention_days(configured_days: int) -> int:
    return max(MIN_RETENTION_DAYS, configured_days)


def _has_existing_cache(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _state_count(state: dict) -> int:
    details = state.get("details") if isinstance(state.get("details"), dict) else {}
    value = details.get("written_rows") or 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def cdc_directory_url(base_url: str, resolution: str, parameter: str, period: str) -> str:
    return f"{base_url.rstrip('/')}/observations_germany/climate/{resolution}/{parameter}/{period}/"


def zip_links_for_station(html: str, station_id: str) -> list[str]:
    pattern = re.compile(r'href="([^"]*_' + re.escape(station_id) + r'_[^"]*\.zip)"', re.IGNORECASE)
    return sorted(set(pattern.findall(html)))


def parse_cdc_zip(payload: bytes, dataset: str, station_id: str, source_url: str) -> list[DwdHistoricalRecord]:
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            records: list[DwdHistoricalRecord] = []
            for name in archive.namelist():
                if not _is_product_file(name):
                    continue
                text = archive.read(name).decode("latin-1")
                records.extend(parse_cdc_text(text, dataset, station_id, source_url))
            return records
    except zipfile.BadZipFile as exc:
        raise DwdHistoricalError("DWD CDC payload is not a valid ZIP file.") from exc


def parse_cdc_text(text: str, dataset: str, station_id: str, source_url: str) -> list[DwdHistoricalRecord]:
    reader = csv.DictReader(text.splitlines(), delimiter=";")
    records: list[DwdHistoricalRecord] = []
    for row in reader:
        normalized = {key.strip(): (value or "").strip() for key, value in row.items() if key}
        if normalized.get("STATIONS_ID", "").zfill(5) != station_id.zfill(5):
            continue
        timestamp = _record_time(normalized)
        quality = normalized.get("QN") or normalized.get("QN_3") or normalized.get("QN_4") or ""
        for field, raw_value in normalized.items():
            if field in SKIP_COLUMNS or raw_value in {"", "-999", "-999.0"}:
                continue
            try:
                value = float(raw_value.replace(",", "."))
            except ValueError:
                continue
            records.append(
                DwdHistoricalRecord(
                    time=timestamp,
                    station_id=station_id.zfill(5),
                    dataset=dataset,
                    field=field,
                    value=value,
                    quality=quality,
                    source_url=source_url,
                )
            )
    return records


def read_historical_rows(path: Path) -> list[dict[str, str]]:
    return [row for row in read_dwd_rows(path) if row.get("kind") == "observation"]


def write_historical_rows(path: Path, rows: list[dict[str, str]]) -> None:
    write_dwd_rows(path, merge_dwd_rows(rows))


def merge_historical_rows(
    existing_rows: list[dict[str, str]],
    fetched_rows: list[dict[str, str]],
    cutoff: datetime,
) -> list[dict[str, str]]:
    merged: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in [*existing_rows, *fetched_rows]:
        row_time = _row_time(row)
        if row_time is None or row_time < cutoff:
            continue
        key = (row["time"], row["station_id"], row["dataset"], row["field"])
        merged[key] = {column: row.get(column, "") for column in HISTORICAL_COLUMNS}
    return sorted(merged.values(), key=lambda row: (row["time"], row["station_id"], row["dataset"], row["field"]))


def _is_product_file(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(".txt") and ("produkt" in lowered or "stundenwerte" in lowered or "10minutenwerte" in lowered)


def _record_time(row: dict[str, str]) -> datetime:
    raw = row.get("MESS_DATUM") or row.get("MESS_DATUM_BEGINN") or row.get("MESS_DATUM_ENDE")
    if not raw:
        raise DwdHistoricalError("CDC row has no MESS_DATUM column.")
    return _parse_dwd_timestamp(raw)


def _parse_dwd_timestamp(value: str) -> datetime:
    value = value.strip()
    formats = {12: "%Y%m%d%H%M", 10: "%Y%m%d%H", 8: "%Y%m%d"}
    fmt = formats.get(len(value))
    if not fmt:
        raise DwdHistoricalError(f"Unsupported DWD timestamp: {value}")
    return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)


def _row_time(row: dict[str, str]) -> datetime | None:
    value = row.get("time")
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
