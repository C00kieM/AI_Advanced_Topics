# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python 3.11+ `src/` layout package named `weather-ai`. Main code lives in `src/weather_ai/`; tests live in `tests/`; small committed fixtures live in `tests/fixtures/`.

Core modules:

- `config.py`: `.env` loading and typed `Settings`. Add every new environment key here and in `.env.example`.
- `influx.py`: read-only InfluxDB access and Flux query builders. Influx writes are explicitly blocked.
- `local_cache.py`, `strunde_cache.py`, `dwd_historical.py`, `forecast_archive.py`, `dwd_data.py`: local CSV stores, merge/dedupe/prune logic, and offline fallbacks.
- `dwd.py`, `mosmix.py`: forecast adapters. MOSMIX is primary when `MOSMIX_STATION_ID` is configured; DWD JSON fallback remains available.
- `comparison.py`, `ml.py`, `daily_profile.py`, `service.py`: forecast-vs-observation matching, model training, daily profiles, and orchestration.
- `chat.py`, `strunde.py`, `stations.py`: rule-based German chat, Strunde water-level logic, and station scoping.
- `api.py`, `jobs.py`, `gui_status.py`: FastAPI endpoints, in-memory background jobs, and GUI payload shaping.
- `gui.py`, `static/index.html`, `static/app.js`, `static/styles.css`: pywebview launcher and no-build GUI.

Local runtime artifacts are intentionally untracked: `data/`, `.env`, `.weather-ai-models/`, `output/`, pytest temp folders, and generated caches.

## Architecture & Data Flow

The app is local-first and read-only against InfluxDB. Weather station rows are cached into `data/local_weather_history.csv`; DWD observations and forecast archives share `data/dwd_weather_data.csv`; Strunde levels use `data/strunde_water_level.csv`. These CSVs are operational data, not source files.

Typical flow:

1. CLI/API/GUI command starts a job or chat request.
2. Cache layer reads local CSV first and only syncs from Influx/DWD through known adapters.
3. `WeatherService` compares archived forecasts with local observations and trains correction models.
4. `ChatService` answers normal weather questions, historical ranges, station-specific questions, and routes Strunde questions to `StrundeService`.
5. GUI calls `/chat`, `/status`, and `/jobs`; it must never expose arbitrary shell execution.

## Build, Test, and Development Commands

Install:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Run tests:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; python -m pytest -q --tb=short
```

Use targeted tests while developing:

```powershell
python -m pytest tests/test_chat.py -q
python -m pytest tests/test_gui_api.py tests/test_gui_status.py -q
python -m pytest tests/test_strunde.py tests/test_strunde_cache.py -q
```

Run locally:

```powershell
weather-chat status
weather-chat sync-local-db
weather-chat sync-dwd-history
weather-chat sync-strunde
weather-chat archive-dwd-forecast
weather-chat compare
weather-chat train
weather-chat chat
weather-gui
```

## Coding Style & Naming Conventions

Use 4-space indentation, type hints, dataclasses for structured records, and small pure helpers for parsing/formatting. Prefer existing service/cache APIs over ad hoc CSV scans. Keep user-facing chat and GUI text German unless the surrounding file is already English. Keep source comments sparse and useful.

Naming patterns:

- Test files: `tests/test_<area>.py`.
- Fixtures: descriptive CSV names, for example `strunde_water_level.csv`.
- Cache result dataclasses: `<Area>SyncResult`.
- API slash commands: canonical lowercase strings such as `/sync-strunde`.

Be careful with German umlauts. Existing files mostly use ASCII transliterations (`fuer`, `ueber`, `Strunde-Pegel`) because some files already contain encoding artifacts.

## Testing Guidelines

The suite uses pytest and currently covers chat, CLI payloads, comparison, DWD, MOSMIX, GUI API/status, jobs, local cache, ML, station scoping, and Strunde. Add tests for every behavioral change.

Required patterns:

- Mock network access. Do not rely on live InfluxDB, DWD, or MOSMIX in tests.
- Test offline behavior for every sync/cache feature.
- Test GUI payload changes in `test_gui_api.py` or `test_gui_status.py`.
- Test chat routing and phrasing in `test_chat.py` or `test_strunde.py`.
- Keep fixtures small. Never commit real `data/*.csv`.
- On Windows, avoid pytest `tmp_path` if ACL cleanup becomes flaky; use controlled workspace fixtures or cleanup-tolerant runtime paths.

Before handing off, run the full command:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; python -m pytest -q --tb=short
```

## GUI Rules

The GUI is a desktop app launched by `weather-gui`; it is not a public website. `create_app()` serves GUI assets only when `gui_enabled=True` and a generated `gui_token` is provided.

Admin View is dark, operational, and may show status cards, job logs, timings, warnings, and command buttons. Chat View is intentionally light and simple: chat history, input, send button, no technical statusbar, no terminal-style logs. If you add GUI fields, update all three static files plus GUI/API tests. Bump `ASSET_VERSION` in `api.py` when changing static assets.

## Security & Configuration

Never commit `.env`, tokens, local CSV data, model files, or generated output. InfluxDB is read-only; keep `InfluxClient.write_forecasts()` and raw write endpoint blocking intact. New file-open targets must be explicit in `_open_file_targets()` and must point only to known local app data files.

Use `OFFLINE_MODE=true` when InfluxDB/DWD/MOSMIX are unavailable or when tests must not fetch live data. In offline mode, chat and GUI status should use local CSVs plus saved `comparison_summary.json` / `training_metrics.json`; do not run sync or archive commands unless live access is explicitly intended.

If adding configuration, update:

- `Settings` in `config.py`
- `.env.example`
- README/Context if the setting affects operation
- tests for default and configured behavior

## Commit & Pull Request Guidelines

Git history mixes short messages and Conventional Commit style (`fix`, `feat: ...`, `Refactor ...`). Prefer concise imperative messages:

- `feat: add strunde cache status`
- `fix: avoid dwd status timeout`
- `test: cover station scoped training`

Pull requests should include the behavior summary, changed commands/endpoints, test command and result, screenshots for GUI changes, and any `.env.example` or data-file implications.

## Agent-Specific Instructions

Before editing, inspect the relevant module and tests; this repo has several interacting paths where a small change can break GUI, CLI, and chat simultaneously. Do not revert unrelated user changes. Do not delete or rewrite files under `data/`. Use `rg` for searches and exclude runtime folders when scanning. Prefer `apply_patch` for source edits. After changing behavior, update docs and tests in the same turn.
