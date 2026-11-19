"""List the tables and row counts in the database, and the applied migration.

Run from the project root:  python scripts/check_schema.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402

from database.connection import get_engine  # noqa: E402
from database.models import Base  # noqa: E402

engine = get_engine()
existing = set(inspect(engine).get_table_names())
expected = set(Base.metadata.tables)
with engine.connect() as conn:
    for name in sorted(expected):
        if name in existing:
            n = conn.execute(text(f'select count(*) from "{name}"')).scalar_one()
            print(f"  OK       {name:20s} rows: {n}")
        else:
            print(f"  MISSING  {name}")
    if "alembic_version" in existing:
        print("Migration version:", conn.execute(text("select version_num from alembic_version")).scalar_one())
sys.exit(0 if expected <= existing else 1)
