"""Общий контракт адаптера источника: скачать -> сохранить сырьё -> разобрать в записи."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.engine import Engine

from common import config
from services.ingest import http
from services.ingest.store import mark_parsed, save_raw

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    source: str
    files: int = 0
    changed: int = 0
    new_items: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "IngestResult") -> None:
        self.files += other.files
        self.changed += other.changed
        self.new_items += other.new_items
        self.skipped += other.skipped
        self.errors += other.errors

    def as_dict(self) -> dict:
        return {"source": self.source, "files": self.files, "changed": self.changed,
                "new_items": self.new_items, "skipped": self.skipped, "errors": self.errors}


class Adapter:
    name: str = ""
    parser_version: str = "1"
    has_live: bool = True
    has_backfill: bool = False

    @property
    def cfg(self) -> dict:
        return config.source_cfg(self.name)

    # --- переопределяется в адаптерах ---
    def live(self, engine: Engine) -> IngestResult:
        raise NotImplementedError

    def backfill(self, engine: Engine, start: date, end: date) -> IngestResult:
        raise NotImplementedError(f"{self.name}: загрузка архива не поддерживается")

    def parse(self, conn, raw_id: int, content: bytes, url: str, fetched_at: datetime) -> int:
        """Разбирает сырьё и пишет записи. Возвращает число новых записей."""
        raise NotImplementedError

    # --- общее ---
    def fetch_and_store(self, engine: Engine, url: str, result: IngestResult, *, not_found_ok: bool = False,
                        response: http.Response | None = None, source: str | None = None) -> None:
        resp = response or http.get(url, not_found_ok=not_found_ok)
        if resp is None:
            result.skipped += 1
            return
        self.store_response(engine, url, resp, result, source=source)

    def store_response(self, engine: Engine, url: str, resp: http.Response, result: IngestResult,
                       source: str | None = None) -> None:
        with engine.begin() as conn:
            raw_id, changed = save_raw(conn, source or self.name, url, resp.content, resp.content_type,
                                       resp.status, self.parser_version)
            result.files += 1
            if not changed:
                return
            result.changed += 1
            from common.timeutil import utcnow
            try:
                with conn.begin_nested():
                    n = self.parse(conn, raw_id, resp.content, url, utcnow())
                mark_parsed(conn, raw_id, "ok", n)
                result.new_items += n
            except Exception as e:  # сырьё сохраняем даже при ошибке разбора
                log.exception("%s: ошибка разбора %s", self.name, url)
                mark_parsed(conn, raw_id, "error", 0, f"{type(e).__name__}: {e}")
                result.errors.append(f"разбор {url}: {e}")
