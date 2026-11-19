"""Verify the app can reach the Neon database.

Run from the project root:  python scripts/check_db_connection.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.connection import check_connection  # noqa: E402

start = time.time()
try:
    info = check_connection()
except Exception as exc:  # noqa: BLE001
    print(f"FAILED: {type(exc).__name__}: {exc}")
    sys.exit(1)
print(f"OK in {time.time() - start:.1f}s")
print(f"Database: {info['database']}")
print(f"Server:   {info['version']}")
