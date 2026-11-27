"""Small JSON files in the data folder, for things the app itself records
(approvals, deck edits, settings entered in the UI). Writes are atomic."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .config import settings

_lock = threading.Lock()


def _path(name: str) -> Path:
    return settings.data_dir / f"{name}.json"


def read_json(name: str, default: Any) -> Any:
    path = _path(name)
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(name: str, value: Any) -> None:
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with _lock:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
