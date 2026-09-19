"""Состояние источников: заморозка снимается сама; новая колонка добавляется в существующую БД."""
from datetime import timedelta

from sqlalchemy import create_engine, inspect, text, update

from common.db import get_engine, init_db, source_status
from common.timeutil import utcnow
from services.ingest.store import is_paused, set_paused


def test_pause_expires(tmp_path):
    eng = get_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(eng)
    with eng.begin() as conn:
        set_paused(conn, "goes_protons", True)
        assert is_paused(conn, "goes_protons")
        conn.execute(update(source_status).where(source_status.c.source == "goes_protons")
                     .values(paused_at=utcnow() - timedelta(hours=3)))
        assert not is_paused(conn, "goes_protons")  # заморозка старше 2 ч снята
        assert not is_paused(conn, "goes_protons")


def test_old_schema_gets_new_column(tmp_path):
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    raw = create_engine(url)
    with raw.begin() as conn:  # таблица в виде первой версии схемы, без paused_at
        conn.execute(text("CREATE TABLE source_status (source VARCHAR(64) PRIMARY KEY, status VARCHAR(16) NOT NULL, "
                          "last_attempt DATETIME, last_success DATETIME, last_new_data DATETIME, last_error TEXT, "
                          "consecutive_failures INTEGER, paused BOOLEAN, updated_at DATETIME)"))
    eng = get_engine(url)
    init_db(eng)
    assert "paused_at" in {c["name"] for c in inspect(eng).get_columns("source_status")}
