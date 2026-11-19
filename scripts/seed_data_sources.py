"""Insert the reviewed data sources (safe to run more than once).

Run from the project root:  python scripts/seed_data_sources.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session  # noqa: E402

from database.connection import get_engine  # noqa: E402
from database.seed import seed_data_sources  # noqa: E402

with Session(get_engine()) as session:
    added = seed_data_sources(session)
    session.commit()
print(f"Data sources added: {added} (0 means they already existed)")
