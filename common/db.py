"""Схема БД (SQLAlchemy Core) и общие операции.

Работает с PostgreSQL (сервер) и SQLite (локальная разработка, тесты).
Все времена — naive UTC.
"""
from __future__ import annotations

import json
from functools import lru_cache

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, Text,
                        UniqueConstraint, Index, create_engine, event, text)
from sqlalchemy.engine import Engine

from common import config

metadata = MetaData()

raw_files = Table(
    "raw_files", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(64), nullable=False),
    Column("url", Text, nullable=False),
    Column("fetched_at", DateTime, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("size", Integer, nullable=False),
    Column("content_type", String(128)),
    Column("path", Text, nullable=False),
    Column("http_status", Integer),
    Column("parser_version", String(32)),
    Column("parse_status", String(32)),
    Column("n_items", Integer),
    Column("note", Text),
    Index("ix_raw_source_url", "source", "url"),
)

# Числовые значения: наблюдения, прогнозы, вероятности
records = Table(
    "records", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(64), nullable=False),
    Column("product", String(64), nullable=False),
    Column("quantity", String(64), nullable=False),
    Column("unit", String(32)),
    Column("value", Float),
    Column("level", String(32)),
    Column("valid_from", DateTime, nullable=False),
    Column("valid_to", DateTime, nullable=False),
    Column("issued_at", DateTime, nullable=False),
    Column("issued_at_policy", String(16), nullable=False),  # exact | estimated | fetched | reconstructed
    Column("fetched_at", DateTime, nullable=False),
    Column("kind", String(16), nullable=False),  # observation | forecast
    Column("region", String(16)),  # geo | planetary | sun | iss_orbit
    Column("quality", JSON),
    Column("raw_id", Integer),
    Column("locator", String(128)),
    UniqueConstraint("source", "product", "quantity", "valid_from", "issued_at", name="uq_records"),
    Index("ix_records_q_time", "quantity", "valid_from"),
    Index("ix_records_issued", "issued_at"),
)

# Текстовые сообщения: алерты, предупреждения, сводки SWPC
messages = Table(
    "messages", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(64), nullable=False),
    Column("code", String(16), nullable=False),
    Column("serial", Integer),
    Column("issued_at", DateTime, nullable=False),
    Column("msg_type", String(32)),  # ALERT | WARNING | EXTENDED WARNING | WATCH | SUMMARY | CANCEL...
    Column("title", Text),
    Column("begin_time", DateTime),
    Column("max_time", DateTime),
    Column("end_time", DateTime),
    Column("valid_from", DateTime),
    Column("valid_to", DateTime),
    Column("extension_of", Integer),
    Column("cancel_of", Integer),
    Column("scale", String(64)),
    Column("max_flux", Float),
    Column("text", Text),
    Column("fetched_at", DateTime, nullable=False),
    Column("raw_id", Integer),
    Column("locator", String(128)),
    UniqueConstraint("source", "code", "serial", "issued_at", name="uq_messages"),
    Index("ix_messages_issued", "issued_at"),
)

# Орбитальные элементы (МКС и объекты каталога)
elements = Table(
    "elements", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("norad_id", Integer, nullable=False),
    Column("object_name", String(64)),
    Column("object_type", String(32)),
    Column("epoch", DateTime, nullable=False),
    Column("creation_date", DateTime, nullable=False),
    Column("creation_policy", String(16), nullable=False),  # exact | fetched
    Column("line1", Text, nullable=False),
    Column("line2", Text, nullable=False),
    Column("periapsis", Float),
    Column("apoapsis", Float),
    Column("source", String(64), nullable=False),
    Column("fetched_at", DateTime, nullable=False),
    Column("raw_id", Integer),
    UniqueConstraint("norad_id", "epoch", "creation_date", "source", name="uq_elements"),
    Index("ix_elements_norad_creation", "norad_id", "creation_date"),
    Index("ix_elements_creation", "creation_date"),
    Index("ix_elements_epoch", "epoch"),
)

source_status = Table(
    "source_status", metadata,
    Column("source", String(64), primary_key=True),
    Column("status", String(16), nullable=False),  # ok | stale | error | paused | never
    Column("last_attempt", DateTime),
    Column("last_success", DateTime),
    Column("last_new_data", DateTime),
    Column("last_error", Text),
    Column("consecutive_failures", Integer, default=0),
    Column("paused", Boolean, default=False),
    Column("paused_at", DateTime),              # когда заморожен (автовозобновление — config/sources.yaml, ingest)
    Column("updated_at", DateTime),
)

runs = Table(
    "runs", metadata,
    Column("id", String(40), primary_key=True),
    Column("kind", String(16), nullable=False),  # assessment | verify
    Column("parent_id", String(40)),
    Column("created_at", DateTime, nullable=False),
    Column("finished_at", DateTime),
    Column("status", String(16), nullable=False),  # queued | running | done | failed
    Column("progress", JSON),
    Column("request", JSON),
    Column("result", JSON),
    Column("error", Text),
    Column("algorithm_version", String(32)),
    Column("summary", JSON),                    # краткий итог для списка сохранённых расчётов
    Column("label", Text),                      # название, данное пользователем
)


# Отслеживаемые окна (утверждённый план или идущий выход)
watches = Table(
    "watches", metadata,
    Column("id", String(40), primary_key=True),
    Column("created_at", DateTime, nullable=False),
    Column("mode", String(16), nullable=False),  # now | replay (проигрывание исторического дня)
    Column("label", Text),
    Column("request", JSON, nullable=False),     # параметры расчёта окна
    Column("window_start", DateTime, nullable=False),
    Column("window_end", DateTime, nullable=False),
    Column("sim_time", DateTime),                # для replay: текущий момент «часов»
    Column("sim_step_min", Integer),
    Column("sim_end", DateTime),
    Column("status", String(16), nullable=False),  # active | finished | expired | stopped
    Column("channels", JSON),
    Column("last_check_at", DateTime),
    Column("last_snapshot", JSON),
    Column("n_alerts", Integer, default=0),
    Column("error", Text),
)

alerts = Table(
    "alerts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("watch_id", String(40), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("as_of", DateTime, nullable=False),         # на какой момент оценка (для replay — «часы»)
    Column("severity", String(16), nullable=False),    # critical | warning | info
    Column("kind", String(32), nullable=False),
    Column("mechanism", String(32)),
    Column("message", Text, nullable=False),
    Column("details", JSON),
    Column("data_latest_issued", DateTime),
    Column("data_lag_s", Integer),                     # давность самых свежих данных на момент оценки
    Column("delivered", JSON),
    Column("ack_at", DateTime),
    Index("ix_alerts_watch", "watch_id", "id"),
)


# Дни архива, которых нет у поставщика (404, пропуски архива): не запрашиваются повторно до recheck
archive_days = Table(
    "archive_days", metadata,
    Column("source", String(64), primary_key=True),
    Column("day", String(10), primary_key=True),       # YYYY-MM-DD
    Column("status", String(16), nullable=False),      # unavailable
    Column("checked_at", DateTime, nullable=False),
)


def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    url = url or config.DATABASE_URL
    if url.startswith("sqlite"):
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"timeout": 30, "check_same_thread": False},
                               json_serializer=lambda o: json.dumps(o, ensure_ascii=False))
        event.listen(engine, "connect", _sqlite_pragmas)
    else:
        engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5,
                               json_serializer=lambda o: json.dumps(o, ensure_ascii=False))
    return engine


# колонки, добавленные после первой версии схемы: create_all не меняет существующие таблицы
_ADDED_COLUMNS = {"source_status": {"paused_at": "TIMESTAMP"}, "runs": {"summary": "JSON", "label": "TEXT"}}


def _add_missing_columns(engine: Engine) -> None:
    from sqlalchemy import inspect

    insp = inspect(engine)
    for table, cols in _ADDED_COLUMNS.items():
        have = {c["name"] for c in insp.get_columns(table)}
        for name, sqltype in cols.items():
            if name not in have:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}"))


def init_db(engine: Engine | None = None) -> None:
    """Создаёт таблицы. Несколько сервисов стартуют одновременно — при гонке повторяем."""
    import time

    for attempt in range(6):
        try:
            engine = engine or get_engine()
            metadata.create_all(engine)
            _add_missing_columns(engine)
            return
        except Exception:
            if attempt == 5:
                raise
            time.sleep(1 + attempt)


def insert_ignore(conn, table: Table, rows: list[dict]) -> int:
    """Вставка с пропуском дублей по уникальному ключу. Возвращает число новых строк."""
    if not rows:
        return 0
    dialect = conn.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError(f"Неподдерживаемая БД: {dialect}")
    inserted = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        result = conn.execute(insert(table).on_conflict_do_nothing(), chunk)
        if result.rowcount and result.rowcount > 0:
            inserted += result.rowcount
    return inserted


def notify(conn, channel: str, payload: str) -> None:
    """Сигнал другим сервисам (PostgreSQL LISTEN/NOTIFY). В SQLite — ничего не делает."""
    if conn.dialect.name == "postgresql":
        conn.execute(text("SELECT pg_notify(:c, :p)"), {"c": channel, "p": payload})
