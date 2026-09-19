"""API сервиса сбора данных: статусы источников, обновление, сырьё, выгрузка CSV."""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import func, select

from common import config
from common.db import elements, get_engine, init_db, messages, raw_files, records
from common.freshness import sources_overview
from common.timeutil import iso, parse_iso, utcnow
from services.ingest import bootstrap, registry
from services.ingest.store import set_paused

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

_sched = None  # планировщик опроса — для «следующее обновление» в консоли
EXPORT_TABLES = {"records": records, "messages": messages, "elements": elements, "raw_files": raw_files}
_TIME_COL = {"records": "valid_from", "messages": "issued_at", "elements": "epoch", "raw_files": "fetched_at"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _sched
    init_db()
    if os.getenv("INGEST_SCHEDULER", "1") == "1":
        from services.ingest.scheduler import build_scheduler
        _sched = build_scheduler()
        _sched.start()
    if os.getenv("INGEST_BOOTSTRAP", "1") == "1":
        bootstrap.start_background()  # догрузка архива: пустая БД — весь период, иначе только недостающие дни
    yield
    if _sched:
        _sched.shutdown(wait=False)
        _sched = None


app = FastAPI(title="ingest — сбор данных", lifespan=lifespan)


@app.get("/health")
def health():
    with get_engine().connect() as conn:
        conn.execute(select(1))
    return {"status": "ok"}


@app.get("/sources")
def list_sources(detail: bool = False):
    """Состояние источников. detail=1 — для раздела «Источники» консоли: описание, адреса,
    следующий плановый опрос, объём сохранённого."""
    with get_engine().connect() as conn:
        out = sources_overview(conn)
        stats = _source_stats(conn) if detail else {}
    for s in out:
        s["refresh"] = REFRESH.get(s["source"])
        if not detail:
            continue
        cfg = config.source_cfg(s["source"])
        adapter = registry.ADAPTERS.get(s["source"])
        job = _sched.get_job(s["source"]) if _sched else None
        s.update({k: cfg.get(k) for k in ("group", "provider", "what", "used_for", "event_driven")})
        s["urls"] = _source_urls(cfg)
        s["has_live"] = bool(adapter and adapter.has_live)
        s["has_backfill"] = bool(adapter and adapter.has_backfill)
        s["next_run"] = iso(job.next_run_time.astimezone(timezone.utc).replace(tzinfo=None)) \
            if job and job.next_run_time else None
        s["stats"] = stats.get(s["source"], {})
    return out


def _source_urls(cfg: dict) -> list[dict]:
    out = []
    for key, title in (("live_url", "текущие данные"), ("endpoint", "текущие данные"), ("archive_url", "архив"),
                       ("meta_url", "описание выпуска"), ("flux_url", "почасовой поток"), ("doc_url", "текст прогноза")):
        if cfg.get(key):
            out.append({"title": title, "url": cfg[key]})
    for hours, url in (cfg.get("catchup_urls") or {}).items():
        out.append({"title": f"догрузка пропуска до {hours} ч", "url": url})
    return out


def _source_stats(conn) -> dict[str, dict]:
    """Сколько сохранено по каждому источнику: сырые файлы и разобранные записи."""
    out: dict[str, dict] = {}
    for r in conn.execute(select(raw_files.c.source, func.count(), func.sum(raw_files.c.size),
                                 func.max(raw_files.c.fetched_at)).group_by(raw_files.c.source)):
        out[r[0]] = {"raw_files": r[1], "raw_bytes": int(r[2] or 0), "last_fetch": iso(r[3])}
    counts = dict(conn.execute(select(records.c.source, func.count()).group_by(records.c.source)).fetchall())
    counts.update(conn.execute(select(messages.c.source, func.count()).group_by(messages.c.source)).fetchall())
    is_iss = elements.c.norad_id == ISS_NORAD
    for src, iss, n in conn.execute(select(elements.c.source, is_iss, func.count()).group_by(elements.c.source, is_iss)):
        name = "iss_celestrak" if src == "celestrak" else "iss_spacetrack" if iss else "catalog_spacetrack"
        counts[name] = counts.get(name, 0) + n
    for name, n in counts.items():
        out.setdefault(name, {})["items"] = n
    return out


# ---------- ручное обновление из консоли ----------

REFRESH: dict[str, dict] = {}  # источник -> {state: running|done|error, started_at, finished_at, new_items, errors}
_refresh_lock = threading.Lock()


def _refresh_worker(name: str, force: bool) -> None:
    try:
        res = registry.run_live(get_engine(), name, force=force)
        st = {"state": "error" if res.errors and not res.files else "done", "new_items": res.new_items,
              "files": res.files, "errors": res.errors[:5], "skipped": bool(res.skipped)}
    except Exception as e:
        st = {"state": "error", "new_items": 0, "files": 0, "errors": [f"{type(e).__name__}: {e}"]}
    with _refresh_lock:
        REFRESH[name] = {**REFRESH.get(name, {}), **st, "finished_at": iso(utcnow())}


def _start_refresh(name: str, force: bool = True) -> bool:
    """Обновление источника в фоне. False — уже идёт."""
    with _refresh_lock:
        if (REFRESH.get(name) or {}).get("state") == "running":
            return False
        REFRESH[name] = {"state": "running", "started_at": iso(utcnow()), "finished_at": None}
    threading.Thread(target=_refresh_worker, args=(name, force), name=f"refresh-{name}", daemon=True).start()
    return True


def _check_source(name: str) -> None:
    if name not in registry.ADAPTERS:
        raise HTTPException(404, f"Неизвестный источник: {name}")


@app.get("/bootstrap")
def bootstrap_state():
    """Ход догрузки архива при старте: сколько дней по источникам нужно было загрузить, что загружено, ошибки."""
    return bootstrap.STATE


@app.post("/bootstrap", status_code=202)
def bootstrap_rerun():
    """Повторить проверку архива (например, после восстановления сети)."""
    return {"started": bootstrap.start_background(), "state": bootstrap.STATE.get("state")}


@app.post("/sources/refresh-all", status_code=202)
def refresh_all():
    """Обновить все источники с живыми данными. Замороженные пропускаются: заморозка — проверка сбоя для всех."""
    with get_engine().connect() as conn:
        paused = {s["source"] for s in sources_overview(conn) if s["paused"]}
    out = {"started": [], "already_running": [], "skipped_paused": []}
    for name, adapter in registry.ADAPTERS.items():
        if not adapter.has_live:
            continue
        if name in paused:
            out["skipped_paused"].append(name)
        else:
            out["started" if _start_refresh(name, force=False) else "already_running"].append(name)
    return out


@app.post("/sources/{name}/refresh", status_code=202)
def refresh(name: str):
    """Обновить один источник сейчас (замороженный — тоже, один раз)."""
    _check_source(name)
    started = _start_refresh(name, force=True)
    return {"accepted": True, "source": name, "already_running": not started}


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


# Условия Space-Track ограничивают передачу полученных данных третьим лицам: наружу отдаётся только запись
# объекта, на которую ссылается доказательство (МКС — всегда, её элементы публикуются открыто и на CelesTrak).
SPACETRACK_SOURCES = {"iss_spacetrack", "catalog_spacetrack"}
ISS_NORAD = 25544


@app.get("/raw/{raw_id}")
def raw_file(raw_id: int, locator: str | None = None):
    with get_engine().connect() as conn:
        row = conn.execute(select(raw_files).where(raw_files.c.id == raw_id)).first()
    if row is None:
        raise HTTPException(404, "Нет такого файла")
    path = config.RAW_DIR / row.path
    if not path.exists():
        raise HTTPException(410, "Файл отсутствует в хранилище")
    if row.source in SPACETRACK_SOURCES:
        norad = ISS_NORAD if row.source == "iss_spacetrack" else None
        if locator and locator.startswith("NORAD_CAT_ID:"):
            norad = int(locator.split(":", 1)[1])
        if norad is None:
            raise HTTPException(403, "Файл Space-Track целиком не раздаётся (условия использования Space-Track); "
                                     "запись объекта: ?locator=NORAD_CAT_ID:<номер>")
        items = [x for x in json.loads(path.read_bytes()) if str(x.get("NORAD_CAT_ID")) == str(norad)]
        return JSONResponse({"source": row.source, "raw_id": raw_id, "url": row.url, "fetched_at": row.fetched_at.isoformat() + "Z",
                             "note": "выдержка из файла Space-Track: только записи этого объекта", "records": items})
    return FileResponse(path, media_type=(row.content_type or "application/octet-stream").split(";")[0])


@app.get("/export/{table}.csv")
def export_csv(table: str, source: str | None = None, quantity: str | None = None,
               time_from: str | None = Query(None, alias="from"), time_to: str | None = Query(None, alias="to"),
               limit: int = Query(200_000, le=1_000_000)):
    if table not in EXPORT_TABLES:
        raise HTTPException(404, f"Таблицы для выгрузки: {', '.join(EXPORT_TABLES)}")
    t = EXPORT_TABLES[table]
    q = select(t)
    if table == "elements":  # каталог Space-Track наружу не выгружается (условия использования) — только МКС
        q = q.where(t.c.norad_id == ISS_NORAD)
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
