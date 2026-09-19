"""Планировщик: каждый источник опрашивается в своём ритме (config/sources.yaml).

Первый опрос после запуска — по давности последней успешной загрузки: давно (или никогда) — сразу,
со сдвигом между источниками; недавно — когда подойдёт срок. Пропуск за время простоя адаптер
догружает сам (см. live() адаптеров).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from common import config
from common.db import get_engine, source_status
from common.timeutil import utcnow
from services.ingest import registry

log = logging.getLogger(__name__)


def first_run(now: datetime, last_success: datetime | None, interval_s: float, k: int) -> datetime:
    soon = now + timedelta(seconds=5 + 10 * k)
    if last_success is None or (now - last_success).total_seconds() >= interval_s:
        return soon
    return max(last_success + timedelta(seconds=interval_s), soon)


def build_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1,
                                                               "misfire_grace_time": 300})
    engine = get_engine()
    now = utcnow()
    with engine.connect() as conn:
        last = {r.source: r.last_success for r in conn.execute(select(source_status.c.source,
                                                                      source_status.c.last_success))}
    plan = {}
    for k, (name, adapter) in enumerate(registry.ADAPTERS.items()):
        cfg = config.sources().get(name, {})
        if not adapter.has_live or not cfg.get("interval_s"):
            continue
        t = first_run(now, last.get(name), cfg["interval_s"], k)
        plan[name] = t
        sched.add_job(registry.run_live, "interval", seconds=int(cfg["interval_s"]), args=[engine, name],
                      id=name, next_run_time=t)
    log.info("Первый опрос источников: %s", {n: f"через {(t - now).total_seconds():.0f} с" for n, t in plan.items()})
    return sched
