"""Досбор архива при старте ingest.

Периоды archive.ranges из config/sources.yaml: по каждому источнику находятся дни без данных, и они загружаются.
Пустая БД — загружается весь период; после перезапуска — только недостающее. Дни, которых нет у поставщика
(404, пропуски архива NCEI), запоминаются в archive_days и повторно запрашиваются не чаще recheck_days.
Сбой сети не считается отсутствием данных: такие дни будут запрошены при следующем запуске.

Живые данные досбирает планировщик (scheduler.first_run): источник, который давно не обновлялся, опрашивается
сразу, а адаптер сам догружает пропуск за время простоя.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine

from common import config
from common.db import archive_days, get_engine, notify
from common.timeutil import iso, utcnow
from services.ingest import registry

log = logging.getLogger(__name__)

STATE: dict = {"state": "idle"}
_lock = threading.Lock()


def days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=k) for k in range((end - start).days + 1)]


def missing_ranges(start: date, end: date, have: set[date]) -> list[tuple[date, date]]:
    """Непрерывные отрезки дней из [start, end], которых нет в have."""
    out, a = [], None
    for d in days(start, end):
        if d not in have and a is None:
            a = d
        elif d in have and a is not None:
            out.append((a, d - timedelta(days=1)))
            a = None
    if a is not None:
        out.append((a, end))
    return out


def _unavailable(conn, source: str, recheck_days: float) -> set[date]:
    since = utcnow() - timedelta(days=recheck_days)
    rows = conn.execute(select(archive_days.c.day).where(archive_days.c.source == source,
                                                         archive_days.c.checked_at >= since))
    return {date.fromisoformat(r.day) for r in rows}


def _mark_unavailable(conn, source: str, missing: list[date]) -> None:
    if not missing:
        return
    keys = [d.isoformat() for d in missing]
    conn.execute(delete(archive_days).where(archive_days.c.source == source, archive_days.c.day.in_(keys)))
    now = utcnow()
    conn.execute(archive_days.insert(), [{"source": source, "day": k, "status": "unavailable", "checked_at": now}
                                         for k in keys])


def archive_ranges() -> list[tuple[date, date]]:
    cfg = config.sources().get("archive") or {}
    ranges = cfg.get("ranges") or ([[cfg["from"], cfg["to"]]] if cfg.get("from") else [])
    return [(date.fromisoformat(a), date.fromisoformat(b)) for a, b in ranges]


def ensure_archive(engine: Engine | None = None, start: date | None = None, end: date | None = None,
                   sources: list[str] | None = None, adapters: dict | None = None) -> dict:
    """Догружает дни архива без данных (по умолчанию — все периоды archive.ranges). Сводка — в STATE."""
    engine = engine or get_engine()
    periods = [(start, end)] if start and end else archive_ranges()
    recheck = (config.sources().get("archive") or {}).get("recheck_days", 7)
    adapters = adapters or registry.ADAPTERS
    names = [n for n in (sources or registry.BACKFILL_ORDER) if n in adapters and adapters[n].has_backfill]

    # план: что уже есть, чего нет у поставщика, что загружать
    plan = {n: [] for n in names}
    with engine.connect() as conn:
        for name in names:
            skip = _unavailable(conn, name, recheck)
            for a, b in periods:
                plan[name] += missing_ranges(a, b, adapters[name].covered_days(conn, a, b) | skip)
    total = sum((b - a).days + 1 for r in plan.values() for a, b in r)
    STATE.update(state="running", started_at=iso(utcnow()), finished_at=None,
                 ranges=[{"from": a.isoformat(), "to": b.isoformat()} for a, b in periods],
                 total_days=total, done_days=0, current=None,
                 sources={n: {"missing_days": sum((b - a).days + 1 for a, b in r), "loaded": [], "unavailable_days": 0,
                              "errors": []} for n, r in plan.items()})
    log.info("Архив %s: дней к загрузке по источникам: %s", [f"{a}–{b}" for a, b in periods],
             {n: s["missing_days"] for n, s in STATE["sources"].items()})

    for name in names:
        ad, st = adapters[name], STATE["sources"][name]
        for a, b in plan[name]:
            STATE["current"] = f"{name} {a}–{b}"
            try:
                res = ad.backfill(engine, a, b)
            except Exception as e:  # noqa: BLE001 - retry next startup; never mark a failed archive range complete
                log.warning("%s: архив %s–%s не загружен: %s", name, a, b, e)
                st["errors"].append(f"{a}–{b}: {e}")
                STATE["done_days"] += (b - a).days + 1
                continue
            st["loaded"].append({"from": a.isoformat(), "to": b.isoformat(), "new_items": res.new_items})
            if res.new_items:
                with engine.begin() as conn:
                    notify(conn, "new_data", name)
            if res.errors:
                st["errors"].extend(res.errors[:5])
            else:
                with engine.begin() as conn:
                    now_have = ad.covered_days(conn, a, b)
                    gone = [d for d in days(a, b) if d not in now_have]
                    _mark_unavailable(conn, name, gone)
                st["unavailable_days"] += len(gone)
            STATE["done_days"] += (b - a).days + 1
    STATE.update(state="errors" if any(s["errors"] for s in STATE["sources"].values()) else "done",
                 finished_at=iso(utcnow()), current=None)
    log.info("Архив: готово (%s), загружено дней-источников %d", STATE["state"], total)
    return STATE


def start_background() -> bool:
    """Запускает досбор архива в фоне; повторный вызов во время работы ничего не делает."""
    if not _lock.acquire(blocking=False):
        return False

    def run():
        try:
            ensure_archive()
        except Exception as e:  # noqa: BLE001 - bootstrap state must expose failure and allow a later retry
            log.exception("Досбор архива упал")
            STATE.update(state="errors", finished_at=iso(utcnow()), error=f"{type(e).__name__}: {e}")
        finally:
            _lock.release()

    STATE.update(state="running", started_at=iso(utcnow()))
    threading.Thread(target=run, name="archive-bootstrap", daemon=True).start()
    return True
