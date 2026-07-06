from __future__ import annotations

import argparse
import json
import sys

from .chat import ChatService
from .config import Settings
from .diagnostics import build_status, format_status
from .dwd_historical import DwdHistoricalCsvStore, DwdHistoricalSyncResult
from .local_cache import CacheSyncResult, WeatherStationCsvCache, sync_cache_on_startup
from .service import WeatherService
from .strunde_cache import StrundeCacheSyncResult, StrundeLevelCsvCache


try:
    import typer
except Exception:  # noqa: BLE001
    typer = None


def _cmd_status() -> None:
    settings = _settings_with_startup_sync()
    print(format_status(build_status(settings)))


def _cmd_archive_dwd_forecast() -> None:
    settings = _settings_with_startup_sync()
    result = WeatherService(settings).archive_dwd_forecast()
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _cmd_compare() -> None:
    settings = Settings.from_env()
    result = WeatherService(settings).latest_comparison_summary()
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _cmd_train() -> None:
    settings = Settings.from_env()
    result = WeatherService(settings).train()
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _cmd_chat() -> None:
    settings = _settings_with_startup_sync()
    service = ChatService(settings)
    print("Wetter-KI Chat. Mit 'exit' oder 'quit' beenden.")
    while True:
        question = input("Du: ")
        if question.strip().lower() in {"exit", "quit", "q"}:
            break
        print(service.answer(question))


def _cmd_sync_local_db() -> None:
    settings = Settings.from_env()
    result = WeatherStationCsvCache(settings).sync()
    print(json.dumps(_cache_result_payload(result), indent=2, ensure_ascii=False))


def _cmd_sync_dwd_history() -> None:
    settings = Settings.from_env()
    result = DwdHistoricalCsvStore(settings).sync()
    print(json.dumps(_dwd_history_result_payload(result), indent=2, ensure_ascii=False))


def _cmd_sync_strunde() -> None:
    settings = Settings.from_env()
    result = StrundeLevelCsvCache(settings).sync()
    print(json.dumps(_strunde_result_payload(result), indent=2, ensure_ascii=False))


def _settings_with_startup_sync() -> Settings:
    settings = Settings.from_env()
    try:
        sync_cache_on_startup(settings)
    except Exception as exc:  # noqa: BLE001 - cache sync must not block diagnostics/chat.
        print(f"Lokaler CSV-Cache konnte nicht synchronisiert werden: {exc}", file=sys.stderr)
    return settings


def _cache_result_payload(result: CacheSyncResult) -> dict[str, str | int | bool | None]:
    payload: dict[str, str | int | bool | None] = {
        "path": str(result.path),
        "existing_rows": result.existing_rows,
        "fetched_rows": result.fetched_rows,
        "written_rows": result.written_rows,
        "cutoff": result.cutoff.isoformat(),
        "started_at": result.started_at.isoformat(),
        "skipped": result.skipped,
    }
    if result.reason:
        payload["reason"] = result.reason
    if result.warning:
        payload["warning"] = result.warning
    return payload


def _dwd_history_result_payload(result: DwdHistoricalSyncResult) -> dict[str, str | int | bool | list[str] | None]:
    payload: dict[str, str | int | bool | list[str] | None] = {
        "path": str(result.path),
        "station_ids": result.station_ids,
        "fetched_records": result.fetched_records,
        "written_rows": result.written_rows,
        "cutoff": result.cutoff.isoformat(),
        "skipped": result.skipped,
    }
    if result.reason:
        payload["reason"] = result.reason
    if result.warning:
        payload["warning"] = result.warning
    return payload


def _strunde_result_payload(result: StrundeCacheSyncResult) -> dict[str, str | int | bool | None]:
    payload: dict[str, str | int | bool | None] = {
        "path": str(result.path),
        "existing_rows": result.existing_rows,
        "fetched_rows": result.fetched_rows,
        "written_rows": result.written_rows,
        "cutoff": result.cutoff.isoformat(),
        "started_at": result.started_at.isoformat(),
        "skipped": result.skipped,
    }
    if result.reason:
        payload["reason"] = result.reason
    if result.warning:
        payload["warning"] = result.warning
    return payload


if typer is not None:
    app = typer.Typer(help="Wetter-KI MVP CLI")

    @app.command()
    def status() -> None:
        """Prueft InfluxDB, DWD und lokale Datenaktualitaet."""
        _cmd_status()

    @app.command("archive-dwd-forecast")
    def archive_dwd_forecast() -> None:
        """Ruft DWD-Prognosen ab und archiviert sie lokal als CSV."""
        _cmd_archive_dwd_forecast()

    @app.command()
    def compare() -> None:
        """Zeigt eine aktuelle DWD-vs-lokal-Zusammenfassung."""
        _cmd_compare()

    @app.command()
    def train() -> None:
        """Trainiert lokale Korrekturmodelle, falls genug Daten vorhanden sind."""
        _cmd_train()

    @app.command()
    def chat() -> None:
        """Startet einen interaktiven Terminal-Chat."""
        _cmd_chat()

    @app.command("sync-local-db")
    def sync_local_db() -> None:
        """Synchronisiert die lokale 3-Jahres-CSV fuer alle Wetterstations-Measurements."""
        _cmd_sync_local_db()

    @app.command("sync-dwd-history")
    def sync_dwd_history() -> None:
        """Laedt historische DWD-CDC-Daten und schreibt sie in eine lokale CSV."""
        _cmd_sync_dwd_history()

    @app.command("sync-strunde")
    def sync_strunde() -> None:
        """Synchronisiert Strunde-Pegeldaten aus InfluxDB in eine lokale CSV."""
        _cmd_sync_strunde()


def _argparse_main() -> None:
    parser = argparse.ArgumentParser(description="Wetter-KI MVP CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "archive-dwd-forecast", "compare", "train", "chat", "sync-local-db", "sync-dwd-history", "sync-strunde"):
        subparsers.add_parser(command)
    args = parser.parse_args()
    commands = {
        "status": _cmd_status,
        "archive-dwd-forecast": _cmd_archive_dwd_forecast,
        "compare": _cmd_compare,
        "train": _cmd_train,
        "chat": _cmd_chat,
        "sync-local-db": _cmd_sync_local_db,
        "sync-dwd-history": _cmd_sync_dwd_history,
        "sync-strunde": _cmd_sync_strunde,
    }
    commands[args.command]()


def main() -> None:
    if typer is not None:
        app()
    else:
        _argparse_main()


if __name__ == "__main__":
    main()
