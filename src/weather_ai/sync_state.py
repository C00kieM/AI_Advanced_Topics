from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json


def sync_state_path(data_path: Path, name: str) -> Path:
    return data_path.with_name(f"{data_path.name}.{name}.sync.json")


def read_sync_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_sync_state(path: Path, *, attempted_at: datetime, status: str, details: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "attempted_at": attempted_at.astimezone(timezone.utc).isoformat(),
        "attempted_date": attempted_at.astimezone(timezone.utc).date().isoformat(),
        "status": status,
    }
    if details:
        payload["details"] = details
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def attempted_today(state: dict[str, Any], now: datetime) -> bool:
    expected = now.astimezone(timezone.utc).date().isoformat()
    return state.get("attempted_date") == expected
