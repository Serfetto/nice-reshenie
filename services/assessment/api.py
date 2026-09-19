"""API сервиса оценки окон."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import Body, FastAPI, HTTPException, Query
from sqlalchemy import select, update

from common import config
from common.db import alerts, get_engine, init_db, runs, watches
from common.freshness import sources_overview
from common.timeutil import iso, utcnow
from services.assessment import jobs, watch
from services.assessment.runner import RunError
from services.assessment.schemas import RunRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    jobs.fail_interrupted()
    if os.getenv("WATCHER", "1") == "1":
        watch.start_background()
    yield


app = FastAPI(title="assessment — оценка окон ВКД", lifespan=lifespan)

HEAVY = ("series", "timeline", "evidence", "manifest", "windows")


@app.get("/health")
def health():
    with get_engine().connect() as conn:
        conn.execute(select(1))
    return {"status": "ok", "algorithm_version": config.ALGORITHM_VERSION}


@app.get("/config")
def get_config():
    """Действующие пороги и параметры — для прозрачности расчёта."""
    return {"thresholds": config.thresholds(), "algorithm_version": config.ALGORITHM_VERSION}


@app.post("/runs", status_code=202)
def create_run(req: RunRequest):
    if req.mode != "review":
        # прогноз выбирает из будущих вариантов: окно, начатое до момента расчёта, рекомендовать нельзя
        t = req.as_of if req.mode == "replay" else utcnow() - timedelta(minutes=15)
        for title, v in (("Самое раннее начало", req.earliest_start), ("Плановое начало", req.planned_start)):
            if v is not None and v < t:
                raise HTTPException(422, f"{title} {iso(v)} раньше момента расчёта {iso(req.as_of or utcnow())}: "
                                         "варианты для прогноза начинаются не раньше него. "
                                         "Чтобы разобрать прошедшее, включите «Разбор».")
    run_id = jobs.submit_assessment(req)
    return {"run_id": run_id, "status": "queued"}


def _row_dict(row, include: set[str] | None) -> dict:
    d = {"run_id": row.id, "kind": row.kind, "parent_id": row.parent_id, "status": row.status,
         "created_at": iso(row.created_at), "finished_at": iso(row.finished_at), "progress": row.progress,
         "request": row.request, "error": row.error, "algorithm_version": row.algorithm_version}
    if row.result is not None:
        res = row.result
        if include is not None:
            res = {k: v for k, v in res.items() if k not in HEAVY or k in include}
        d["result"] = res
    return d


@app.get("/runs/{run_id}")
def get_run(run_id: str, include: str | None = Query(None, description="summary: без тяжёлых полей; "
                                                                        "или список полей через запятую")):
    with get_engine().connect() as conn:
        row = conn.execute(select(runs).where(runs.c.id == run_id)).first()
    if row is None:
        raise HTTPException(404, "Нет такого расчёта")
    inc = None if include is None else ({x for x in include.split(",") if x} if include != "summary" else set())
    return _row_dict(row, inc)


@app.get("/runs")
def list_runs(limit: int = Query(20, le=200), parent_id: str | None = None):
    q = select(runs.c.id, runs.c.kind, runs.c.parent_id, runs.c.status, runs.c.created_at, runs.c.request, runs.c.error)
    if parent_id:
        q = q.where(runs.c.parent_id == parent_id)
    with get_engine().connect() as conn:
        rows = conn.execute(q.order_by(runs.c.created_at.desc()).limit(limit)).fetchall()
    return [{"run_id": r.id, "kind": r.kind, "parent_id": r.parent_id, "status": r.status,
             "created_at": iso(r.created_at), "request": r.request, "error": r.error} for r in rows]


@app.post("/runs/{run_id}/verify", status_code=202)
def verify_run(run_id: str):
    try:
        vid = jobs.submit_verify(run_id)
    except KeyError:
        raise HTTPException(404, "Нет такого расчёта")
    except RunError as e:
        raise HTTPException(409, str(e))
    return {"run_id": vid, "status": "queued", "parent_id": run_id}


# ---------- отслеживание и оповещения ----------

def _watch_dict(w) -> dict:
    return {"watch_id": w.id, "mode": w.mode, "label": w.label, "status": w.status,
            "created_at": iso(w.created_at), "window_start": iso(w.window_start), "window_end": iso(w.window_end),
            "sim_time": iso(w.sim_time), "sim_step_min": w.sim_step_min, "sim_end": iso(w.sim_end),
            "channels": w.channels, "last_check_at": iso(w.last_check_at), "n_alerts": w.n_alerts,
            "request": w.request, "last_snapshot": w.last_snapshot, "error": w.error}


@app.post("/watches", status_code=201)
def create_watch(body: dict = Body(..., examples=[{
        "mode": "replay", "window_start": "2024-06-08T06:00:00Z", "duration_min": 390,
        "as_of": "2024-06-07T22:00:00Z", "sim_step_min": 30, "channels": ["web"], "label": "ВКД-1"}])):
    try:
        wid = watch.create(body)
    except (RunError, KeyError, ValueError) as e:
        raise HTTPException(422, str(e))
    return {"watch_id": wid}


@app.get("/watches")
def list_watches(limit: int = Query(20, le=200)):
    with get_engine().connect() as conn:
        rows = conn.execute(select(watches).order_by(watches.c.created_at.desc()).limit(limit)).fetchall()
    return [_watch_dict(w) for w in rows]


@app.get("/watches/{wid}")
def get_watch(wid: str):
    with get_engine().connect() as conn:
        w = conn.execute(select(watches).where(watches.c.id == wid)).first()
    if w is None:
        raise HTTPException(404, "Нет такого отслеживания")
    return _watch_dict(w)


@app.post("/watches/{wid}/stop")
def stop_watch(wid: str):
    if not watch.stop(wid):
        raise HTTPException(404, "Нет активного отслеживания с таким id")
    return {"watch_id": wid, "status": "stopped"}


@app.get("/alerts")
def list_alerts(watch_id: str | None = None, since_id: int = 0, limit: int = Query(100, le=500)):
    q = select(alerts).where(alerts.c.id > since_id)
    if watch_id:
        q = q.where(alerts.c.watch_id == watch_id)
    with get_engine().connect() as conn:
        rows = conn.execute(q.order_by(alerts.c.id).limit(limit)).fetchall()
    return [{"id": a.id, "watch_id": a.watch_id, "created_at": iso(a.created_at), "as_of": iso(a.as_of),
             "severity": a.severity, "kind": a.kind, "mechanism": a.mechanism, "message": a.message,
             "details": a.details, "data_latest_issued": iso(a.data_latest_issued), "data_lag_s": a.data_lag_s,
             "delivered": a.delivered, "ack_at": iso(a.ack_at)} for a in rows]


@app.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: int):
    with get_engine().begin() as conn:
        res = conn.execute(update(alerts).where(alerts.c.id == alert_id).values(ack_at=utcnow()))
    if not res.rowcount:
        raise HTTPException(404, "Нет такого оповещения")
    return {"id": alert_id, "acknowledged": True}


@app.get("/sources")
def sources():
    """Состояние источников (то же, что у ingest) — чтобы интерфейсу хватало одного адреса."""
    with get_engine().connect() as conn:
        return sources_overview(conn)
