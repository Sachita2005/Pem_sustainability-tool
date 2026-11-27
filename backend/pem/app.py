"""The web server: JSON API under /api, the single-page front end at /.

Each area of the app is a module in `pem.domains` that exposes a FastAPI
`router`. A module that fails to import is reported at /api/health instead of
taking the whole site down, so one broken source file cannot hide the rest.
"""
from __future__ import annotations

import importlib
import traceback

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import PROJECT_ROOT, settings
from .plants import PLANTS

DOMAINS = ["energy", "production", "manual", "tbal", "water", "scrap", "air", "initiatives", "ai", "deck", "milestones"]

app = FastAPI(title="PEM Sustainability", docs_url="/api/docs", openapi_url="/api/openapi.json")
_loaded: dict[str, str] = {}

for _name in DOMAINS:
    try:
        _module = importlib.import_module(f"pem.domains.{_name}")
        app.include_router(_module.router)
        _loaded[_name] = "ok"
    except ModuleNotFoundError as exc:
        _loaded[_name] = "not built yet" if exc.name == f"pem.domains.{_name}" else f"error: {exc}"
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        _loaded[_name] = f"error: {exc}"
        traceback.print_exc()


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "sourceDir": str(settings.source_dir),
        "sourceDirFound": settings.source_dir.is_dir(),
        "domains": _loaded,
    }


@app.get("/api/plants")
def plants() -> list[dict]:
    return PLANTS


FRONTEND = PROJECT_ROOT / "frontend"
if FRONTEND.is_dir():
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-store"})
