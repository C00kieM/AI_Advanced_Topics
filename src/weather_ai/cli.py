from __future__ import annotations

import argparse
import json

from .chat import ChatService
from .config import Settings
from .diagnostics import build_status, format_status
from .service import WeatherService


try:
    import typer
except Exception:  # noqa: BLE001
    typer = None


def _cmd_status() -> None:
    settings = Settings.from_env()
    print(format_status(build_status(settings)))


def _cmd_ingest_dwd() -> None:
    settings = Settings.from_env()
    result = WeatherService(settings).ingest_dwd()
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
    settings = Settings.from_env()
    service = ChatService(settings)
    print("Wetter-KI Chat. Mit 'exit' oder 'quit' beenden.")
    while True:
        question = input("Du: ")
        if question.strip().lower() in {"exit", "quit", "q"}:
            break
        print(service.answer(question))


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


def _argparse_main() -> None:
    parser = argparse.ArgumentParser(description="Wetter-KI MVP CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "ingest-dwd", "compare", "train", "chat"):
        subparsers.add_parser(command)
    args = parser.parse_args()
    commands = {
        "status": _cmd_status,
        "ingest-dwd": _cmd_ingest_dwd,
        "compare": _cmd_compare,
        "train": _cmd_train,
        "chat": _cmd_chat,
    }
    commands[args.command]()


def main() -> None:
    if typer is not None:
        app()
    else:
        _argparse_main()


if __name__ == "__main__":
    main()
