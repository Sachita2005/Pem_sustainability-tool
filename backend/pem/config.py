"""Settings for the PEM Sustainability app.

Everything that differs between machines is read from environment variables
(or from a `.env` file next to the project's README), so nothing in the code
has to change when the app moves to a company server.

  PEM_SOURCE_DIR        folder holding the source files (common/, energy/, water/ ...)
  PEM_DATA_DIR          folder the app writes to (manual entries, edited deck, approvals, caches)
  PEM_HOST / PEM_PORT   where the web server listens

  TBAL_ION_URL          base URL of the TBAL ION meter server (blank = use the snapshot workbook)
  TBAL_ION_USER / TBAL_ION_PASSWORD / TBAL_ION_SERVER_ID
  TBAL_ION_POLL_SECONDS how often live readings are refreshed (default 900 = 15 minutes)

  PEM_AI_PROVIDER       "local" (built-in model) or "internal" (company AI server)
  PEM_AI_URL / PEM_AI_API_KEY / PEM_AI_MODEL   used when the provider is "internal"
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_env_file(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines; real environment variables win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    source_dir: Path
    data_dir: Path
    host: str
    port: int
    tbal_ion_url: str
    tbal_ion_user: str
    tbal_ion_password: str
    tbal_ion_server_id: str
    tbal_ion_poll_seconds: int
    ai_provider: str
    ai_url: str
    ai_api_key: str
    ai_model: str

    @property
    def common_dir(self) -> Path:
        return self.source_dir / "common"


def _resolve_source_dir(path: Path) -> Path:
    """Accept the folder that holds `common/`, or a folder one level above it
    (an unzipped archive often wraps everything in a folder of the same name)."""
    if (path / "common").is_dir() or not path.is_dir():
        return path
    nested = [d for d in path.iterdir() if d.is_dir() and (d / "common").is_dir()]
    return nested[0] if len(nested) == 1 else path


def load_settings() -> Settings:
    env = os.environ.get
    return Settings(
        source_dir=_resolve_source_dir(Path(env("PEM_SOURCE_DIR", str(PROJECT_ROOT / "source"))).expanduser()),
        data_dir=Path(env("PEM_DATA_DIR", str(PROJECT_ROOT / "data"))).expanduser(),
        host=env("PEM_HOST", "127.0.0.1"),
        port=int(env("PEM_PORT", "8800")),
        tbal_ion_url=env("TBAL_ION_URL", ""),
        tbal_ion_user=env("TBAL_ION_USER", ""),
        tbal_ion_password=env("TBAL_ION_PASSWORD", ""),
        tbal_ion_server_id=env("TBAL_ION_SERVER_ID", ""),
        tbal_ion_poll_seconds=int(env("TBAL_ION_POLL_SECONDS", "900")),
        ai_provider=env("PEM_AI_PROVIDER", "local").lower(),
        ai_url=env("PEM_AI_URL", ""),
        ai_api_key=env("PEM_AI_API_KEY", ""),
        ai_model=env("PEM_AI_MODEL", ""),
    )


settings = load_settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)


def find_source_file(subdir: str, *name_parts: str, suffix: str = ".xlsx") -> Path | None:
    """Find a source file by words in its name, so a renamed copy ("... (1).xlsx") still resolves.

    Returns the newest file in `source_dir/subdir` whose name contains every
    part (case-insensitive) and ends with `suffix`. Office lock files are ignored.
    """
    folder = settings.source_dir / subdir
    if not folder.is_dir():
        return None
    wanted = [p.lower() for p in name_parts]
    matches = [
        f for f in folder.iterdir()
        if f.is_file()
        and f.suffix.lower() == suffix.lower()
        and not f.name.startswith(("~$", ".~"))
        and all(w in f.name.lower() for w in wanted)
    ]
    return max(matches, key=lambda f: f.stat().st_mtime) if matches else None
