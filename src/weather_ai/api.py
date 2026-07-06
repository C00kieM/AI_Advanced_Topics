from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
import subprocess
import sys

from .chat import ChatService
from .config import Settings
from .diagnostics import build_status
from .dwd_historical import DwdHistoricalCsvStore
from .gui_status import build_gui_status
from .jobs import JobManager
from .local_cache import WeatherStationCsvCache, sync_cache_on_startup
from .service import WeatherService
from .stations import StationScope, station_scope_from_question
from .strunde_cache import StrundeLevelCsvCache


try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse
    from pydantic import BaseModel
except Exception as exc:  # noqa: BLE001
    FastAPI = None  # type: ignore[assignment]
    BaseModel = object  # type: ignore[assignment,misc]
    _FASTAPI_IMPORT_ERROR = exc
else:
    _FASTAPI_IMPORT_ERROR = None


ASSET_VERSION = "20260703-strunde1"

COMMANDS = [
    {
        "command": "/info",
        "description": "Zeigt diese Befehlsuebersicht.",
    },
    {
        "command": "/status",
        "description": "Aktualisiert Systemlage, lokale CSV, DWD-Daten, Vergleich und Modelle.",
    },
    {
        "command": "/sync-local",
        "description": "Synchronisiert lokale Wetterdaten aus InfluxDB in die CSV.",
    },
    {
        "command": "/sync-dwd",
        "description": "Synchronisiert historische DWD-CDC-Daten.",
    },
    {
        "command": "/sync-strunde",
        "description": "Synchronisiert Strunde-Pegeldaten aus InfluxDB in die lokale CSV.",
    },
    {
        "command": "/archive",
        "description": "Archiviert aktuelle DWD/MOSMIX-Prognosen lokal.",
    },
    {
        "command": "/compare",
        "description": "Berechnet den Forecast-vs-Ist-Vergleich.",
    },
    {
        "command": "/train",
        "description": "Trainiert lokale Korrekturmodelle, falls genug Vergleichspaare vorhanden sind.",
    },
    {
        "command": "/clear",
        "description": "Leert den Chatverlauf in der GUI.",
    },
]

COMMAND_SET = {item["command"] for item in COMMANDS}


if FastAPI is not None:
    class ChatRequest(BaseModel):
        question: str

    class TerminalCommandRequest(BaseModel):
        command: str

    class OpenFileRequest(BaseModel):
        target: str


def create_app(
    settings: Settings | None = None,
    sync_on_startup: bool = True,
    gui_enabled: bool = False,
    gui_token: str | None = None,
):
    if FastAPI is None:
        raise RuntimeError(f"FastAPI is not installed: {_FASTAPI_IMPORT_ERROR}")
    settings = settings or Settings.from_env()
    service = WeatherService(settings)
    chat_service = ChatService(settings)
    jobs = JobManager()
    api = FastAPI(title="Wetter-KI MVP", version="0.1.0")
    api.state.jobs = jobs
    api.state.local_cache_sync_result = None
    api.state.local_cache_sync_error = None

    @api.middleware("http")
    async def no_cache_gui_assets(request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/gui/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response
    if sync_on_startup:
        try:
            api.state.local_cache_sync_result = sync_cache_on_startup(settings)
        except Exception as exc:  # noqa: BLE001 - API should still start if cache sync fails.
            api.state.local_cache_sync_error = str(exc)

    static_dir = Path(__file__).with_name("static")
    if gui_enabled and static_dir.exists():

        def require_gui_token(provided_token: str | None) -> None:
            if gui_token and provided_token != gui_token:
                raise HTTPException(status_code=404, detail="GUI nicht verfuegbar.")

        @api.get("/", response_class=HTMLResponse)
        def index(gui_token: str | None = None):
            require_gui_token(gui_token)
            html = (static_dir / "index.html").read_text(encoding="utf-8")
            return HTMLResponse(
                html.replace("__ASSET_VERSION__", ASSET_VERSION).replace("__GUI_TOKEN__", gui_token or ""),
                headers={"Cache-Control": "no-store"},
            )

        @api.get("/gui/static/{asset_name}")
        def gui_static(asset_name: str, gui_token: str | None = None):
            require_gui_token(gui_token)
            if asset_name not in {"styles.css", "app.js"}:
                raise HTTPException(status_code=404, detail="Asset nicht gefunden.")
            return FileResponse(
                static_dir / asset_name,
                headers={"Cache-Control": "no-store"},
            )

    @api.get("/health")
    def health():
        status = build_status(settings)
        return {
            "ok": status.influx_ok,
            "influx_ok": status.influx_ok,
            "dwd_ok": status.dwd_ok,
            "warnings": status.warnings,
            "local_cache_error": api.state.local_cache_sync_error,
        }

    @api.get("/status")
    def status(live: bool = False, deep: bool = False):
        payload = build_gui_status(settings, live=live, deep=deep)
        payload["startup"] = {
            "local_cache_sync_result": api.state.local_cache_sync_result,
            "local_cache_sync_error": api.state.local_cache_sync_error,
        }
        return payload

    @api.get("/local/latest")
    def local_latest():
        return [asdict(item) for item in service.latest_local()]

    @api.get("/forecast/current")
    def forecast_current():
        return [asdict(item) for item in service.current_forecast()]

    @api.get("/comparison/latest")
    def comparison_latest():
        return service.latest_comparison_summary()

    @api.post("/files/open")
    def open_file(request: OpenFileRequest):
        target = request.target.strip().lower()
        targets = _open_file_targets(settings)
        if target not in targets:
            raise HTTPException(status_code=400, detail="Unbekannte Datei. Erlaubt sind nur lokale App-Daten.")
        label, path = targets[target]
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"{label} wurde noch nicht gefunden: {path}")
        try:
            _open_local_file(path)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"{label} konnte nicht geoeffnet werden: {exc}") from exc
        return {
            "ok": True,
            "target": target,
            "label": label,
            "path": str(path),
        }

    def command_info_payload() -> dict[str, object]:
        lines = ["Moegliche Befehle:"]
        lines.extend([f"{item['command']}: {item['description']}" for item in COMMANDS])
        return {
            "type": "info",
            "answer": "\n".join(lines),
            "commands": COMMANDS,
        }

    def start_command_job(command: str, station_scope: StationScope | None = None):
        if command == "/sync-local":
            return jobs.start("Lokale Wetter-CSV synchronisieren", lambda: WeatherStationCsvCache(settings).sync())
        if command == "/sync-dwd":
            return jobs.start("DWD-CDC-Historie synchronisieren", lambda: DwdHistoricalCsvStore(settings).sync())
        if command == "/sync-strunde":
            return jobs.start("Strunde-Pegel-CSV synchronisieren", lambda: StrundeLevelCsvCache(settings).sync())
        if command == "/archive":
            return jobs.start("DWD-Prognose archivieren", service.archive_dwd_forecast)
        if command == "/train":
            name = "Korrekturmodelle trainieren"
            if station_scope and station_scope.is_single:
                name += f" ({station_scope.label})"
            return jobs.start(name, lambda: service.train(station_scope))
        raise KeyError(command)

    def run_app_command(command: str, raw_value: str | None = None) -> dict[str, object]:
        command = command.strip().lower()
        if command not in COMMAND_SET:
            raise HTTPException(status_code=400, detail=f"Unbekanntes Kommando: {command}")
        station_scope = station_scope_from_question(settings, raw_value or command)
        if station_scope.is_ambiguous:
            return {
                "type": "message",
                "answer": _ambiguous_station_answer(station_scope),
                "station_scope": _station_scope_payload(station_scope),
            }
        if command == "/info":
            return command_info_payload()
        if command == "/clear":
            return {"type": "clear", "answer": ""}
        if command == "/status":
            return {
                "type": "status",
                "answer": "Status aktualisiert.",
                "status": build_gui_status(settings, live=False, deep=False),
            }
        if command == "/compare":
            comparison = service.latest_comparison_summary(station_scope)
            return {
                "type": "comparison",
                "answer": f"Vergleich aktualisiert: {comparison.get('pairs', 0)} Paare.",
                "comparison": comparison,
                "station_scope": _station_scope_payload(station_scope),
            }
        job = start_command_job(command, station_scope)
        return {
            "type": "job",
            "answer": f"Job gestartet: {job.name}.",
            "job": job.to_payload(),
            "command": command,
            "station_scope": _station_scope_payload(station_scope),
        }

    @api.post("/chat")
    def chat(request: ChatRequest):
        question = request.question.strip()
        command = _canonical_command(question)
        if command:
            try:
                return run_app_command(command, question)
            except HTTPException:
                if question.startswith("/"):
                    return {
                        "type": "message",
                        "answer": f"Dieses Kommando kenne ich nicht: {question}. Nutze /info fuer die Befehlsliste.",
                    }
                raise
        action = _natural_action_command(question)
        if action:
            if action in {"/status", "/compare", "/info"}:
                return run_app_command(action, question)
            return {
                "type": "confirmation",
                "answer": f"Ich kann das ausfuehren. Bitte bestaetige mit 'ja', dann starte ich {action}.",
                "command": action,
            }
        return {"type": "message", "answer": chat_service.answer(question)}

    @api.post("/jobs/archive-dwd-forecast")
    def archive_dwd_forecast():
        return start_command_job("/archive").to_payload()

    @api.post("/jobs/train")
    def train():
        return start_command_job("/train").to_payload()

    @api.post("/jobs/sync-local-db")
    def sync_local_db():
        return start_command_job("/sync-local").to_payload()

    @api.post("/jobs/sync-dwd-history")
    def sync_dwd_history():
        return start_command_job("/sync-dwd").to_payload()

    @api.post("/jobs/sync-strunde")
    def sync_strunde():
        return start_command_job("/sync-strunde").to_payload()

    @api.get("/jobs")
    def list_jobs():
        return {"jobs": [job.to_payload() for job in jobs.list()]}

    @api.get("/jobs/{job_id}")
    def get_job(job_id: str):
        try:
            return jobs.get(job_id).to_payload()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job nicht gefunden.") from exc

    @api.post("/terminal/command")
    def terminal_command(request: TerminalCommandRequest):
        command = request.command.strip().lower()
        if not command.startswith("/"):
            raise HTTPException(status_code=400, detail="Nur bekannte Slash-Kommandos sind erlaubt.")
        canonical = _canonical_command(command)
        if canonical is None:
            raise HTTPException(status_code=400, detail=f"Unbekanntes Kommando: {request.command.strip()}")
        return run_app_command(canonical, request.command)

    return api


app = create_app(sync_on_startup=False) if FastAPI is not None else None


def _canonical_command(value: str) -> str | None:
    command = value.strip().lower()
    for candidate in sorted(COMMAND_SET, key=len, reverse=True):
        if command == candidate or command.startswith(candidate + " "):
            return candidate
    return None


def _station_scope_payload(station_scope: StationScope) -> dict[str, object]:
    return {
        "kind": station_scope.kind,
        "label": station_scope.label,
        "measurements": sorted(station_scope.measurements) if station_scope.measurements else None,
        "matches": [
            {"measurement": station.measurement, "label": station.label}
            for station in station_scope.matches
        ],
    }


def _ambiguous_station_answer(station_scope: StationScope) -> str:
    options = ", ".join(f"{item.label} ({item.measurement})" for item in station_scope.matches)
    return f"Ich kann die Station nicht eindeutig zuordnen. Meinst du eine dieser Stationen: {options}?"


def _open_file_targets(settings: Settings) -> dict[str, tuple[str, Path]]:
    return {
        "local-cache": ("Lokale CSV", Path(settings.local_cache_path)),
        "dwd-data": ("DWD-CSV", Path(settings.dwd_data_path)),
        "strunde-cache": ("Strunde-Pegel-CSV", Path(settings.strunde_cache_path)),
    }


def _open_local_file(path: Path) -> None:
    resolved = path.resolve()
    if sys.platform.startswith("win"):
        os.startfile(str(resolved))  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(resolved)])  # noqa: S603,S607
        return
    subprocess.Popen(["xdg-open", str(resolved)])  # noqa: S603,S607


def _natural_action_command(value: str) -> str | None:
    normalized = _normalize(value)
    if not normalized:
        return None
    if any(token in normalized for token in ("hilfe", "befehle", "kommandos", "commands")):
        return "/info"
    if any(token in normalized for token in ("status", "systemlage")):
        return "/status"
    if any(token in normalized for token in ("vergleich", "vergleiche", "abweichung", "compare")):
        return "/compare"
    if "train" in normalized or ("modell" in normalized and "lern" in normalized):
        return "/train"
    if ("sync" in normalized or "aktualisier" in normalized or "synchronisier" in normalized) and (
        "lokal" in normalized or "csv" in normalized or "influx" in normalized
    ):
        return "/sync-local"
    if ("sync" in normalized or "aktualisier" in normalized or "synchronisier" in normalized) and (
        "dwd" in normalized or "historie" in normalized or "cdc" in normalized
    ):
        return "/sync-dwd"
    if ("sync" in normalized or "aktualisier" in normalized or "synchronisier" in normalized) and (
        "strunde" in normalized or "pegel" in normalized or "wasserstand" in normalized
    ):
        return "/sync-strunde"
    if ("archiv" in normalized or "sicher" in normalized) and ("forecast" in normalized or "prognose" in normalized or "dwd" in normalized):
        return "/archive"
    return None


def _normalize(value: str) -> str:
    normalized = value.strip().lower()
    replacements = (
        ("\u00e4", "ae"),
        ("\u00f6", "oe"),
        ("\u00fc", "ue"),
        ("\u00df", "ss"),
    )
    for source, replacement in replacements:
        normalized = normalized.replace(source, replacement)
    return normalized
