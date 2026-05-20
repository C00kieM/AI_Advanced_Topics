from __future__ import annotations

from dataclasses import asdict

from .chat import ChatService
from .config import Settings
from .diagnostics import build_status
from .dwd_historical import DwdHistoricalCsvStore
from .local_cache import sync_cache_on_startup
from .service import WeatherService


try:
    from fastapi import FastAPI
    from pydantic import BaseModel
except Exception as exc:  # noqa: BLE001
    FastAPI = None  # type: ignore[assignment]
    BaseModel = object  # type: ignore[assignment,misc]
    _FASTAPI_IMPORT_ERROR = exc
else:
    _FASTAPI_IMPORT_ERROR = None


if FastAPI is not None:
    class ChatRequest(BaseModel):
        question: str


def create_app():
    if FastAPI is None:
        raise RuntimeError(f"FastAPI is not installed: {_FASTAPI_IMPORT_ERROR}")
    settings = Settings.from_env()
    service = WeatherService(settings)
    chat_service = ChatService(settings)
    api = FastAPI(title="Wetter-KI MVP", version="0.1.0")
    try:
        api.state.local_cache_sync_result = sync_cache_on_startup(settings)
        api.state.local_cache_sync_error = None
    except Exception as exc:  # noqa: BLE001 - API should still start if cache sync fails.
        api.state.local_cache_sync_result = None
        api.state.local_cache_sync_error = str(exc)

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
        return service.archive_dwd_forecast()

    @api.post("/jobs/train")
    def train():
        return service.train()

    @api.post("/jobs/sync-dwd-history")
    def sync_dwd_history():
        result = DwdHistoricalCsvStore(settings).sync()
        return {
            "path": str(result.path),
            "station_ids": result.station_ids,
            "fetched_records": result.fetched_records,
            "written_rows": result.written_rows,
            "cutoff": result.cutoff.isoformat(),
        }

    return api


app = create_app() if FastAPI is not None else None
