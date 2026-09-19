"""Срез данных «что было известно на момент T».

Ядро расчёта получает данные только через этот класс. Правило одно для всех режимов:
запись видна, если её время публикации (issued_at / creation_date) не позже отсечки источника.
- now:    отсечка = текущий момент;
- replay: отсечка = T (прогноз из прошлого);
- review: отсечка = текущий момент, T задаёт только интервал разбора (реконструкция).
Переопределения на один расчёт: disabled — источник отсутствует, frozen@X — виден как на момент X.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from common import config
from common.db import elements, messages, records, source_status
from common.freshness import latest_data_time
from common.timeutil import iso, utcnow

ISS_NORAD = 25544
ELEMENT_SOURCES = {"iss_spacetrack": "spacetrack", "iss_celestrak": "celestrak", "catalog_spacetrack": "spacetrack"}


@dataclass
class Override:
    state: str  # disabled | frozen
    at: datetime | None = None


class DataSlice:
    def __init__(self, engine: Engine, mode: str, as_of: datetime, overrides: dict[str, Override] | None = None):
        if mode not in ("now", "replay", "review"):
            raise ValueError(f"Неизвестный режим: {mode}")
        self.engine = engine
        self.mode = mode
        self.as_of = as_of
        self.data_cutoff = utcnow() if mode == "review" else as_of
        self.overrides = overrides or {}
        self._manifest: dict[str, dict] = defaultdict(lambda: {"raw_ids": set(), "items": 0,
                                                               "excluded_after_cutoff": 0,
                                                               "reconstructed": False, "latest_issued": None})

    # --- отсечка по источнику ---
    def cutoff(self, source: str) -> datetime | None:
        ov = self.overrides.get(source)
        if ov is not None and ov.state == "disabled":
            return None
        c = self.data_cutoff
        if ov is not None and ov.state == "frozen" and ov.at is not None:
            c = min(c, ov.at)
        return c

    def _track(self, source: str, rows, issued_attr: str = "issued_at") -> None:
        m = self._manifest[source]
        for r in rows:
            if getattr(r, "raw_id", None) is not None:
                m["raw_ids"].add(r.raw_id)
            m["items"] += 1
            if getattr(r, "issued_at_policy", None) == "reconstructed":
                m["reconstructed"] = True
            iss = getattr(r, issued_attr, None)
            if iss is not None and (m["latest_issued"] is None or iss > m["latest_issued"]):
                m["latest_issued"] = iss

    def _count_excluded(self, conn, table, source_col_value, time_col, cutoff, extra) -> int:
        q = select(func.count()).select_from(table).where(*extra, time_col > cutoff)
        return conn.execute(q).scalar() or 0

    # --- числовые ряды ---
    def records(self, source: str, quantity: str, t0: datetime, t1: datetime, kind: str) -> list:
        """Записи, пересекающие [t0, t1) и опубликованные до отсечки."""
        cutoff = self.cutoff(source)
        if cutoff is None:
            return []
        base = [records.c.source == source, records.c.quantity == quantity, records.c.kind == kind,
                records.c.valid_to > t0, records.c.valid_from < t1]
        with self.engine.connect() as conn:
            rows = conn.execute(select(records).where(*base, records.c.issued_at <= cutoff)
                                .order_by(records.c.valid_from, records.c.issued_at)).fetchall()
            if self.mode == "replay":
                self._manifest[source]["excluded_after_cutoff"] += self._count_excluded(
                    conn, records, source, records.c.issued_at, cutoff, base)
        self._track(source, rows)
        return rows

    def latest_forecasts(self, sources: list[str], quantity: str, t0: datetime, t1: datetime) -> list:
        """Для каждого интервала — значение из самого свежего выпуска (среди источников) до отсечки."""
        best: dict[datetime, object] = {}
        for s in sources:
            for r in self.records(s, quantity, t0, t1, kind="forecast"):
                cur = best.get(r.valid_from)
                if cur is None or r.issued_at > cur.issued_at:
                    best[r.valid_from] = r
        return [best[k] for k in sorted(best)]

    # --- сообщения SWPC ---
    def messages(self, code_prefixes: tuple[str, ...], since: datetime) -> list:
        source = "swpc_alerts"
        cutoff = self.cutoff(source)
        if cutoff is None:
            return []
        conds = [messages.c.source == source, messages.c.issued_at >= since]
        with self.engine.connect() as conn:
            rows = conn.execute(select(messages).where(*conds, messages.c.issued_at <= cutoff)
                                .order_by(messages.c.issued_at)).fetchall()
            if self.mode == "replay":
                self._manifest[source]["excluded_after_cutoff"] += self._count_excluded(
                    conn, messages, source, messages.c.issued_at, cutoff, conds)
        rows = [r for r in rows if r.code.startswith(code_prefixes)]
        self._track(source, rows)
        return rows

    # --- орбитальные элементы ---
    def iss_elements(self, epoch_not_after: datetime | None = None):
        """Набор элементов МКС с самой поздней эпохой среди опубликованных до отсечки.

        epoch_not_after ограничивает эпоху сверху: в разборе прошлого нельзя брать элементы из будущего
        относительно интервала (распространение назад на месяцы даёт неверную орбиту)."""
        best, best_source = None, None
        with self.engine.connect() as conn:
            for name in ("iss_spacetrack", "iss_celestrak"):
                cutoff = self.cutoff(name)
                if cutoff is None:
                    continue
                conds = [elements.c.norad_id == ISS_NORAD, elements.c.source == ELEMENT_SOURCES[name],
                         elements.c.creation_date <= cutoff]
                if epoch_not_after is not None:
                    conds.append(elements.c.epoch <= epoch_not_after)
                row = conn.execute(select(elements).where(*conds)
                                   .order_by(elements.c.epoch.desc(), elements.c.creation_date.desc()).limit(1)).first()
                if row is not None and (best is None or row.epoch > best.epoch):
                    best, best_source = row, name
        if best is not None:
            self._track(best_source, [best], issued_attr="creation_date")
        return best, best_source

    def catalog_elements(self, t_ref: datetime, max_age_days: float,
                         epoch_not_after: datetime | None = None) -> dict[int, object]:
        """Последний опубликованный набор по каждому объекту каталога (эпоха не старше max_age_days)."""
        name = "catalog_spacetrack"
        cutoff = self.cutoff(name)
        if cutoff is None:
            return {}
        conds = [elements.c.source == ELEMENT_SOURCES[name], elements.c.norad_id != ISS_NORAD,
                 elements.c.creation_date <= cutoff, elements.c.epoch >= t_ref - timedelta(days=max_age_days)]
        if epoch_not_after is not None:
            conds.append(elements.c.epoch <= epoch_not_after)
        with self.engine.connect() as conn:
            rows = conn.execute(select(elements).where(*conds)).fetchall()
        latest: dict[int, object] = {}
        for r in rows:
            cur = latest.get(r.norad_id)
            if cur is None or (r.epoch, r.creation_date) > (cur.epoch, cur.creation_date):
                latest[r.norad_id] = r
        self._track(name, latest.values(), issued_attr="creation_date")
        return latest

    # --- свежесть и манифест ---
    def freshness(self, source: str) -> dict:
        cfg = config.sources().get(source, {})
        cutoff = self.cutoff(source)
        ov = self.overrides.get(source)
        if cutoff is None:
            return {"source": source, "state": "disabled", "latest_data": None, "age_s": None}
        with self.engine.connect() as conn:
            latest = latest_data_time(conn, source, cutoff)
            st = conn.execute(select(source_status).where(source_status.c.source == source)).first() \
                if self.mode == "now" else None
        ref = self.as_of if self.mode != "review" else self.data_cutoff
        max_age = cfg.get("max_age_s")
        if cfg.get("event_driven"):
            # лента событий: в «сейчас» важна давность последней успешной проверки; в архиве — наличие
            # сообщений за последний месяц до отсечки (архив загружен)
            if self.mode == "now" and st is not None and st.last_success:
                basis = st.last_success
            else:
                basis = latest if latest and (cutoff - latest) <= timedelta(days=35) else None
                max_age = None if self.mode != "now" else max_age
            latest = basis
        age = (ref - latest).total_seconds() if latest else None
        if latest is None:
            state = "no_data"
        elif ov is not None and ov.state == "frozen":
            state = "frozen" if not (max_age and age > max_age) else "frozen_stale"
        elif max_age and age is not None and age > max_age and self.mode != "review":
            state = "stale"
        else:
            state = "ok"
        out = {"source": source, "state": state, "latest_data": iso(latest),
               "age_s": int(age) if age is not None else None, "max_age_s": max_age}
        if st is not None and st.status == "error":
            out["last_error"] = st.last_error
            out["fetch_status"] = "error"
        return out

    def is_fresh(self, source: str) -> bool:
        return self.freshness(source)["state"] in ("ok", "frozen")

    def manifest(self) -> dict:
        out = {}
        for s, m in self._manifest.items():
            out[s] = {"raw_ids": sorted(m["raw_ids"])[:200], "n_raw_files": len(m["raw_ids"]),
                      "items": m["items"], "excluded_after_cutoff": m["excluded_after_cutoff"],
                      "reconstructed": m["reconstructed"], "latest_issued": iso(m["latest_issued"]),
                      "cutoff": iso(self.cutoff(s)),
                      "override": (self.overrides[s].state if s in self.overrides else None)}
        return out
