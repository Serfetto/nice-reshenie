"""API сервиса сбора данных: статусы источников, обновление, сырьё, выгрузка CSV."""
from __future__ import annotations

import csv
import io
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select

from common import config
from common.db import elements, get_engine, init_db, messages, raw_files, records
from common.freshness import sources_overview
from common.timeutil import parse_iso
from services.ingest import registry
from services.ingest.store import set_paused

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

EXPORT_TABLES = {"records": records, "messages": messages, "elements": elements, "raw_files": raw_files}
_TIME_COL = {"records": "valid_from", "messages": "issued_at", "elements": "epoch", "raw_files": "fetched_at"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    sched = None
    if os.getenv("INGEST_SCHEDULER", "1") == "1":
        from services.ingest.scheduler import build_scheduler
        sched = build_scheduler()
        sched.start()
    yield
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(title="ingest — сбор данных", lifespan=lifespan)


@app.get("/health")
def health():
    with get_engine().connect() as conn:
        conn.execute(select(1))
    return {"status": "ok"}


@app.get("/sources")
def list_sources():
    with get_engine().connect() as conn:
        return sources_overview(conn)


def _check_source(name: str) -> None:
    if name not in registry.ADAPTERS:
        raise HTTPException(404, f"Неизвестный источник: {name}")


@app.post("/sources/{name}/refresh", status_code=202)
def refresh(name: str):
    _check_source(name)
    threading.Thread(target=registry.run_live, args=(get_engine(), name), kwargs={"force": True},
                     daemon=True).start()
    return {"accepted": True, "source": name}


@app.post("/sources/{name}/pause")
def pause(name: str):
    _check_source(name)
    with get_engine().begin() as conn:
        set_paused(conn, name, True)
    return {"source": name, "paused": True}


@app.post("/sources/{name}/resume")
def resume(name: str):
    _check_source(name)
    with get_engine().begin() as conn:
        set_paused(conn, name, False)
    return {"source": name, "paused": False}


@app.get("/raw/{raw_id}/meta")
def raw_meta(raw_id: int):
    with get_engine().connect() as conn:
        row = conn.execute(select(raw_files).where(raw_files.c.id == raw_id)).mappings().first()
    if row is None:
        raise HTTPException(404, "Нет такого файла")
    return dict(row)


@app.get("/raw/{raw_id}")
def raw_file(raw_id: int):
    with get_engine().connect() as conn:
        row = conn.execute(select(raw_files).where(raw_files.c.id == raw_id)).first()
    if row is None:
        raise HTTPException(404, "Нет такого файла")
    path = config.RAW_DIR / row.path
    if not path.exists():
        raise HTTPException(410, "Файл отсутствует в хранилище")
    return FileResponse(path, media_type=(row.content_type or "application/octet-stream").split(";")[0])


@app.get("/export/{table}.csv")
def export_csv(table: str, source: str | None = None, quantity: str | None = None,
               time_from: str | None = Query(None, alias="from"), time_to: str | None = Query(None, alias="to"),
               limit: int = Query(200_000, le=1_000_000)):
    if table not in EXPORT_TABLES:
        raise HTTPException(404, f"Таблицы для выгрузки: {', '.join(EXPORT_TABLES)}")
    t = EXPORT_TABLES[table]
    q = select(t)
    if source is not None and "source" in t.c:
        q = q.where(t.c.source == source)
    if quantity is not None and table == "records":
        q = q.where(t.c.quantity == quantity)
    tc = t.c[_TIME_COL[table]]
    if time_from:
        q = q.where(tc >= parse_iso(time_from))
    if time_to:
        q = q.where(tc < parse_iso(time_to))
    q = q.order_by(tc).limit(limit)

    def rows():
        buf = io.StringIO()
        w = csv.writer(buf)
        with get_engine().connect() as conn:
            result = conn.execute(q)
            w.writerow(result.keys())
            yield buf.getvalue()
            for r in result:
                buf.seek(0)
                buf.truncate()
                w.writerow([v.isoformat() + "Z" if isinstance(v, datetime) else v for v in r])
                yield buf.getvalue()

    return StreamingResponse(rows(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{table}.csv"'})
