"""Планировщик: каждый источник опрашивается в своём ритме (config/sources.yaml)."""
from __future__ import annotations

import logging
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from common import config
from common.db import get_engine
from common.timeutil import utcnow
from services.ingest import registry

log = logging.getLogger(__name__)


def build_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1,
                                                               "misfire_grace_time": 300})
    engine = get_engine()
    start = utcnow()
    for i, (name, adapter) in enumerate(registry.ADAPTERS.items()):
        cfg = config.sources().get(name, {})
        if not adapter.has_live or not cfg.get("interval_s"):
            continue
        sched.add_job(registry.run_live, "interval", seconds=int(cfg["interval_s"]), args=[engine, name],
                      id=name, next_run_time=start + timedelta(seconds=5 + 10 * i))
    return sched
