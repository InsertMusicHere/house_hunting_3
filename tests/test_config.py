import pytest

import config


def test_scheme_normalized(monkeypatch):
    monkeypatch.setattr(config, "_from_streamlit_secrets", lambda: None)
    monkeypatch.setattr(config, "_from_secrets_file", lambda: None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db?sslmode=require")
    assert config.get_database_url().startswith("postgresql+psycopg://u:p@h/db")


def test_missing_url_raises(monkeypatch):
    monkeypatch.setattr(config, "_from_streamlit_secrets", lambda: None)
    monkeypatch.setattr(config, "_from_secrets_file", lambda: None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        config.get_database_url()
