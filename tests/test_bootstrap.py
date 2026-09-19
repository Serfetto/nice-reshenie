"""Досбор данных при старте: какие дни архива грузить, когда опрашивать живые источники."""
from datetime import date, datetime, timedelta

import pytest

from common.db import get_engine, init_db
from services.ingest.adapters.base import IngestResult
from services.ingest.bootstrap import ensure_archive, missing_ranges
from services.ingest.scheduler import first_run

D0, D1 = date(2024, 5, 1), date(2024, 5, 10)


def test_missing_ranges():
    have = {date(2024, 5, 3), date(2024, 5, 4), date(2024, 5, 8)}
    assert missing_ranges(D0, D1, have) == [(D0, date(2024, 5, 2)), (date(2024, 5, 5), date(2024, 5, 7)),
                                            (date(2024, 5, 9), D1)]
    assert missing_ranges(D0, D1, set()) == [(D0, D1)]  # пустая БД — весь период
    assert missing_ranges(D0, D0, {D0}) == []


def test_first_run_by_last_success():
    now = datetime(2026, 9, 19, 12)
    assert first_run(now, None, 300, 0) == now + timedelta(seconds=5)             # никогда — сразу
    assert first_run(now, now - timedelta(hours=5), 3600, 2) == now + timedelta(seconds=25)  # давно — сразу
    assert first_run(now, now - timedelta(minutes=10), 3600, 0) == now + timedelta(minutes=50)  # свежее — по сроку


class FakeAdapter:
    """Архив, в котором у поставщика нет дня `hole`; `fail` — сбой сети при загрузке."""
    name = "fake"
    has_backfill = True

    def __init__(self, hole=None, fail=False):
        self.have, self.hole, self.fail, self.calls = set(), hole, fail, []

    def covered_days(self, conn, start, end):
        return {d for d in self.have if start <= d <= end}

    def backfill(self, engine, start, end):
        self.calls.append((start, end))
        res = IngestResult(self.name)
        if self.fail:
            res.errors.append("сеть недоступна")
            return res
        d = start
        while d <= end:
            if d != self.hole:
                self.have.add(d)
                res.new_items += 1
            d += timedelta(days=1)
        return res


@pytest.fixture()
def engine(tmp_path):
    eng = get_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(eng)
    return eng


def test_empty_db_loads_all_then_only_missing(engine):
    ad = FakeAdapter(hole=date(2024, 5, 5))
    st = ensure_archive(engine, D0, D1, sources=["fake"], adapters={"fake": ad})
    assert ad.calls == [(D0, D1)] and st["sources"]["fake"]["unavailable_days"] == 1
    # после перезапуска: всё есть, дня нет у поставщика — повторно не запрашивается
    ad.calls.clear()
    ensure_archive(engine, D0, D1, sources=["fake"], adapters={"fake": ad})
    assert ad.calls == []
    # часть данных пропала — догружается только она
    ad.have -= {date(2024, 5, 9), D1}
    ensure_archive(engine, D0, D1, sources=["fake"], adapters={"fake": ad})
    assert ad.calls == [(date(2024, 5, 9), D1)]


def test_network_failure_is_not_marked_unavailable(engine):
    ad = FakeAdapter(fail=True)
    st = ensure_archive(engine, D0, D1, sources=["fake"], adapters={"fake": ad})
    assert st["state"] == "errors" and st["sources"]["fake"]["unavailable_days"] == 0
    ad.fail, ad.calls = False, []
    ensure_archive(engine, D0, D1, sources=["fake"], adapters={"fake": ad})
    assert ad.calls == [(D0, D1)]  # после сбоя — повтор всего периода
