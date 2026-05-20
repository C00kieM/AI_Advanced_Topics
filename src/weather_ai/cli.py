from __future__ import annotations

import argparse
import json
import sys

from .chat import ChatService
from .config import Settings
from .diagnostics import build_status, format_status
from .local_cache import CacheSyncResult, WeatherStationCsvCache, sync_cache_on_startup
from .service import WeatherService


try:
    import typer
except Exception:  # noqa: BLE001
    typer = None


def _cmd_status() -> None:
    settings = _settings_with_startup_sync()
    print(format_status(build_status(settings)))


def _cmd_ingest_dwd() -> None:
    settings = _settings_with_startup_sync()
    result = WeatherService(settings).ingest_dwd()
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _cmd_compare() -> None:
    settings = _settings_with_startup_sync()
    result = WeatherService(settings).latest_comparison_summary()
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _cmd_train() -> None:
    settings = _settings_with_startup_sync()
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


def _settings_with_startup_sync() -> Settings:
    settings = Settings.from_env()
    try:
        sync_cache_on_startup(settings)
    except Exception as exc:  # noqa: BLE001 - cache sync must not block diagnostics/chat.
        print(f"Lokaler CSV-Cache konnte nicht synchronisiert werden: {exc}", file=sys.stderr)
    return settings


def _cache_result_payload(result: CacheSyncResult) -> dict[str, str | int]:
    return {
        "path": str(result.path),
        "existing_rows": result.existing_rows,
        "fetched_rows": result.fetched_rows,
        "written_rows": result.written_rows,
        "cutoff": result.cutoff.isoformat(),
        "started_at": result.started_at.isoformat(),
    }


if typer is not None:
    app = typer.Typer(help="Wetter-KI MVP CLI")

    @app.command()
    def status() -> None:
        """Prueft InfluxDB, DWD und lokale Datenaktualitaet."""
        _cmd_status()

    @app.command("ingest-dwd")
    def ingest_dwd() -> None:
        """Ruft DWD-Prognosen ab und archiviert sie in InfluxDB."""
        _cmd_ingest_dwd()

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


def _argparse_main() -> None:
    parser = argparse.ArgumentParser(description="Wetter-KI MVP CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "ingest-dwd", "compare", "train", "chat", "sync-local-db"):
        subparsers.add_parser(command)
    args = parser.parse_args()
    commands = {
        "status": _cmd_status,
        "ingest-dwd": _cmd_ingest_dwd,
        "compare": _cmd_compare,
        "train": _cmd_train,
        "chat": _cmd_chat,
        "sync-local-db": _cmd_sync_local_db,
    }
    commands[args.command]()


def main() -> None:
    if typer is not None:
        app()
    else:
        _argparse_main()


if __name__ == "__main__":
    main()
