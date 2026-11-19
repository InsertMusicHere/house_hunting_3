"""SQLAlchemy engine for the hosted Neon database."""
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config import get_database_url

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = get_database_url()
        kwargs = {}
        if url.startswith("postgresql"):
            kwargs = dict(
                pool_pre_ping=True,   # Neon suspends when idle; revive dead connections
                pool_recycle=300,
                connect_args={"connect_timeout": 30},  # allow for cold start
            )
        _engine = create_engine(url, **kwargs)
    return _engine


def check_connection() -> dict:
    """Run a trivial query and return server info. Raises on failure."""
    with get_engine().connect() as conn:
        version = conn.execute(text("select version()")).scalar_one()
        db = conn.execute(text("select current_database()")).scalar_one()
    return {"database": db, "version": version}
