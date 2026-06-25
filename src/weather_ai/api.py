from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from .chat import ChatService
from .config import Settings
from .diagnostics import build_status
from .dwd_historical import DwdHistoricalCsvStore
from .gui_status import build_gui_status
from .jobs import JobManager
from .local_cache import WeatherStationCsvCache, sync_cache_on_startup
from .service import WeatherService


try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
except Exception as exc:  # noqa: BLE001
    FastAPI = None  # type: ignore[assignment]
    BaseModel = object  # type: ignore[assignment,misc]
    _FASTAPI_IMPORT_ERROR = exc
else:
    _FASTAPI_IMPORT_ERROR = None


ASSET_VERSION = "20260625-ops3"


if FastAPI is not None:
    class ChatRequest(BaseModel):
        question: str

    class TerminalCommandRequest(BaseModel):
        command: str


def create_app(settings: Settings | None = None, sync_on_startup: bool = True):
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
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response
    if sync_on_startup:
        try:
            api.state.local_cache_sync_result = sync_cache_on_startup(settings)
        except Exception as exc:  # noqa: BLE001 - API should still start if cache sync fails.
            api.state.local_cache_sync_error = str(exc)

    static_dir = Path(__file__).with_name("static")
    if static_dir.exists():
        api.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @api.get("/", response_class=HTMLResponse)
        def index():
            html = (static_dir / "index.html").read_text(encoding="utf-8")
            return HTMLResponse(
                html.replace("__ASSET_VERSION__", ASSET_VERSION),
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

    @api.post("/chat")
    def chat(request: ChatRequest):
        return {"answer": chat_service.answer(request.question)}

    @api.post("/jobs/archive-dwd-forecast")
    def archive_dwd_forecast():
        return jobs.start("DWD-Prognose archivieren", service.archive_dwd_forecast).to_payload()

    @api.post("/jobs/train")
    def train():
        return jobs.start("Korrekturmodelle trainieren", service.train).to_payload()

    @api.post("/jobs/sync-local-db")
    def sync_local_db():
        return jobs.start("Lokale Wetter-CSV synchronisieren", lambda: WeatherStationCsvCache(settings).sync()).to_payload()

    @api.post("/jobs/sync-dwd-history")
    def sync_dwd_history():
        return jobs.start("DWD-CDC-Historie synchronisieren", lambda: DwdHistoricalCsvStore(settings).sync()).to_payload()

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
        if command == "/status":
            return {"type": "status", "status": build_gui_status(settings, live=False, deep=False)}
        if command == "/compare":
            return {"type": "comparison", "comparison": service.latest_comparison_summary()}
        if command == "/sync-local":
            return {"type": "job", "job": jobs.start("Lokale Wetter-CSV synchronisieren", lambda: WeatherStationCsvCache(settings).sync()).to_payload()}
        if command == "/sync-dwd":
            return {"type": "job", "job": jobs.start("DWD-CDC-Historie synchronisieren", lambda: DwdHistoricalCsvStore(settings).sync()).to_payload()}
        if command == "/archive":
            return {"type": "job", "job": jobs.start("DWD-Prognose archivieren", service.archive_dwd_forecast).to_payload()}
        if command == "/train":
            return {"type": "job", "job": jobs.start("Korrekturmodelle trainieren", service.train).to_payload()}
        if command == "/clear":
            return {"type": "clear"}
        raise HTTPException(status_code=400, detail=f"Unbekanntes Kommando: {request.command.strip()}")

    return api


app = create_app(sync_on_startup=False) if FastAPI is not None else None
