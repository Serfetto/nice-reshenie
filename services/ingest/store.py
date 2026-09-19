"""Сырые файлы, журнал загрузок, статусы источников."""
from __future__ import annotations

import hashlib
import mimetypes
from datetime import datetime

from sqlalchemy import select, update

from common import config
from common.db import raw_files, source_status, insert_ignore
from common.timeutil import utcnow


def save_raw(conn, source: str, url: str, content: bytes, content_type: str | None, http_status: int | None,
             parser_version: str, fetched_at: datetime | None = None) -> tuple[int, bool]:
    """Сохраняет файл, если он изменился. Возвращает (raw_id, changed)."""
    sha = hashlib.sha256(content).hexdigest()
    last = conn.execute(
        select(raw_files.c.id, raw_files.c.sha256)
        .where(raw_files.c.source == source, raw_files.c.url == url)
        .order_by(raw_files.c.id.desc()).limit(1)
    ).first()
    if last is not None and last.sha256 == sha:
        return last.id, False

    fetched_at = fetched_at or utcnow()
    ext = _guess_ext(url, content_type)
    rel = f"{source}/{fetched_at:%Y/%m/%d}/{fetched_at:%H%M%S}_{sha[:12]}{ext}"
    path = config.RAW_DIR / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    res = conn.execute(raw_files.insert().values(
        source=source, url=url, fetched_at=fetched_at, sha256=sha, size=len(content),
        content_type=content_type, path=rel, http_status=http_status, parser_version=parser_version,
        parse_status="pending"))
    return res.inserted_primary_key[0], True


def mark_parsed(conn, raw_id: int, status: str, n_items: int, note: str | None = None) -> None:
    conn.execute(update(raw_files).where(raw_files.c.id == raw_id)
                 .values(parse_status=status, n_items=n_items, note=note))


def _guess_ext(url: str, content_type: str | None) -> str:
    tail = url.split("?")[0].rsplit("/", 1)[-1]
    if "." in tail and len(tail.rsplit(".", 1)[1]) <= 5:
        return "." + tail.rsplit(".", 1)[1]
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if ext:
            return ext
    return ".bin"


def _ensure_status_row(conn, source: str) -> None:
    insert_ignore(conn, source_status, [{"source": source, "status": "never", "consecutive_failures": 0,
                                         "paused": False, "updated_at": utcnow()}])


def status_success(conn, source: str, new_items: int) -> None:
    _ensure_status_row(conn, source)
    now = utcnow()
    values = {"status": "ok", "last_attempt": now, "last_success": now, "last_error": None,
              "consecutive_failures": 0, "updated_at": now}
    if new_items:
        values["last_new_data"] = now
    conn.execute(update(source_status).where(source_status.c.source == source).values(**values))


def status_error(conn, source: str, error: str) -> None:
    _ensure_status_row(conn, source)
    row = conn.execute(select(source_status.c.consecutive_failures)
                       .where(source_status.c.source == source)).first()
    fails = (row.consecutive_failures or 0) + 1 if row else 1
    now = utcnow()
    conn.execute(update(source_status).where(source_status.c.source == source).values(
        status="error", last_attempt=now, last_error=error[:2000], consecutive_failures=fails, updated_at=now))


def set_paused(conn, source: str, paused: bool) -> None:
    _ensure_status_row(conn, source)
    now = utcnow()
    conn.execute(update(source_status).where(source_status.c.source == source)
                 .values(paused=paused, paused_at=now if paused else None, updated_at=now))


def pause_limit_min() -> float:
    return float((config.sources().get("ingest") or {}).get("pause_max_min", 120))


def is_paused(conn, source: str) -> bool:
    """Заморожен ли источник. Заморозка общая для всех пользователей, поэтому снимается сама через pause_max_min."""
    row = conn.execute(select(source_status.c.paused, source_status.c.paused_at)
                       .where(source_status.c.source == source)).first()
    if not (row and row.paused):
        return False
    if row.paused_at is not None and (utcnow() - row.paused_at).total_seconds() > pause_limit_min() * 60:
        set_paused(conn, source, False)
        return False
    return True
