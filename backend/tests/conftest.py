"""Shared test setup: tests run against the real source folder named in .env,
and write only to a throwaway data folder."""
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ["PEM_DATA_DIR"] = tempfile.mkdtemp(prefix="pem-test-data-")
