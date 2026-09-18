"""Свежесть данных по источникам (используется панелью источников и расчётом)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from common import config
from common.db import elements, messages, records, source_status
from common.timeutil import iso, utcnow

ISS_NORAD = 25544


def latest_data_time(conn, source: str, as_of: datetime | None = None) -> datetime | None:
    """Время самых свежих данных источника, опубликованных не позже as_of."""
    if source == "swpc_alerts":
        q = select(func.max(messages.c.issued_at)).where(messages.c.source == source)
        if as_of:
            q = q.where(messages.c.issued_at <= as_of)
    elif source in ("iss_spacetrack", "iss_celestrak", "catalog_spacetrack"):
        el_source = "celestrak" if source == "iss_celestrak" else "spacetrack"
        q = select(func.max(elements.c.creation_date)).where(elements.c.source == el_source)
        q = q.where(elements.c.norad_id != ISS_NORAD) if source == "catalog_spacetrack" \
            else q.where(elements.c.norad_id == ISS_NORAD)
        if as_of:
            q = q.where(elements.c.creation_date <= as_of)
    else:
        # для наблюдений — конец последнего интервала, для прогнозов — время выпуска
        col = records.c.issued_at
        q = select(func.max(col)).where(records.c.source == source)
        if as_of:
            q = q.where(records.c.issued_at <= as_of)
    return conn.execute(q).scalar()


def sources_overview(conn) -> list[dict]:
    now = utcnow()
    status_rows = {r.source: r for r in conn.execute(select(source_status)).fetchall()}
    out = []
    for name, cfg in config.sources().items():
        if not isinstance(cfg, dict) or "interval_s" not in cfg:
            continue
        st = status_rows.get(name)
        latest = latest_data_time(conn, name)
        basis = latest
        if cfg.get("event_driven") and st is not None and st.last_success:
            basis = st.last_success  # лента событий: важна давность последней успешной проверки
        age = (now - basis).total_seconds() if basis else None
        max_age = cfg.get("max_age_s")
        if st is not None and st.paused:
            effective = "paused"
        elif st is not None and st.status == "error":
            effective = "error"
        elif latest is None:
            effective = "no_data"
        elif max_age and age is not None and age > max_age:
            effective = "stale"
        else:
            effective = "ok"
        out.append({
            "source": name, "title": cfg.get("title"), "status": effective,
            "interval_s": cfg.get("interval_s"), "max_age_s": max_age,
            "issue_times_utc": cfg.get("issue_times_utc"),
            "latest_data": iso(latest), "age_s": int(age) if age is not None else None,
            "last_success": iso(st.last_success) if st else None,
            "last_attempt": iso(st.last_attempt) if st else None,
            "last_error": st.last_error if st else None,
            "consecutive_failures": st.consecutive_failures if st else 0,
            "paused": bool(st.paused) if st else False,
            "replay": cfg.get("replay"),
        })
    return out
