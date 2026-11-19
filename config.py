"""Central configuration. Secrets are read from Streamlit secrets, never from source code."""
import os
import tomllib
from pathlib import Path

SECRETS_FILE = Path(__file__).parent / ".streamlit" / "secrets.toml"


def _from_streamlit_secrets() -> str | None:
    try:
        import streamlit as st

        return st.secrets["database"]["url"]
    except Exception:
        return None


def _from_secrets_file() -> str | None:
    if not SECRETS_FILE.exists():
        return None
    with SECRETS_FILE.open("rb") as f:
        return tomllib.load(f).get("database", {}).get("url")


def get_database_url() -> str:
    """Return a SQLAlchemy URL using the psycopg (v3) driver.

    Lookup order: Streamlit secrets, local secrets.toml (for plain scripts),
    then the DATABASE_URL environment variable.
    """
    url = _from_streamlit_secrets() or _from_secrets_file() or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "No database URL found. Set [database] url in .streamlit/secrets.toml."
        )
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url
