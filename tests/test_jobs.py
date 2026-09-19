"""Задания расчёта: после перезапуска незавершённые не висят вечно."""
from sqlalchemy import select

from common import config, db
from common.timeutil import utcnow
from services.assessment import jobs


def test_interrupted_runs_marked_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    db.get_engine.cache_clear()
    try:
        db.init_db()
        with db.get_engine().begin() as conn:
            for rid, status in (("r_q", "queued"), ("r_r", "running"), ("r_d", "done")):
                conn.execute(db.runs.insert().values(id=rid, kind="assessment", created_at=utcnow(), status=status))
        assert jobs.fail_interrupted() == 2
        with db.get_engine().connect() as conn:
            st = dict(conn.execute(select(db.runs.c.id, db.runs.c.status)).fetchall())
        assert st == {"r_q": "failed", "r_r": "failed", "r_d": "done"}
    finally:
        db.get_engine.cache_clear()
