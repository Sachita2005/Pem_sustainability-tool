"""Call every GET endpoint that needs no parameters and report what came back.

  .venv\\Scripts\\python.exe tools\\smoke.py            all areas
  .venv\\Scripts\\python.exe tools\\smoke.py water      only /api/water/...

A quick way to see, after any change, that no page's data source is broken:
each line shows the HTTP status, the time taken, the size, and any `notes`
the endpoint attached (the app's way of saying "this part of the source is
missing").
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from pem.app import _loaded, app  # noqa: E402


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    client = TestClient(app)
    print("areas:", ", ".join(f"{k}={v}" for k, v in _loaded.items()))
    failures = 0
    for path, methods in sorted(app.openapi()["paths"].items()):
        get = methods.get("get")
        if not get or "{" in path or (only and not path.startswith(f"/api/{only}")):
            continue
        if any(p.get("required") for p in get.get("parameters", [])):
            continue
        started = time.perf_counter()
        response = client.get(path)
        took = time.perf_counter() - started
        note = ""
        if response.headers.get("content-type", "").startswith("application/json"):
            body = response.json()
            if isinstance(body, dict) and body.get("notes"):
                note = f"  notes: {len(body['notes'])} — {str(body['notes'][0])[:90]}"
        ok = response.status_code == 200
        failures += 0 if ok else 1
        print(f"{'ok ' if ok else 'ERR'} {response.status_code} {took:6.2f}s {len(response.content) / 1024:8.1f} KB  {path}{note}")
    print("all endpoints answered" if not failures else f"{failures} endpoint(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
