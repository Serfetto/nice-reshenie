"""Разделы консоли: сохранённые расчёты, оповещения, ручное обновление источников."""
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from common import config, db
from common.timeutil import utcnow
from services.ingest.adapters.base import IngestResult


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    db.get_engine.cache_clear()
    db.init_db()
    yield db.get_engine()
    db.get_engine.cache_clear()


def _window(wid, start, end, status, rad="acceptable", reasons=None):
    return {"id": wid, "start": start, "end": end, "status": status, "is_planned": False,
            "mechanisms": {"radiation": {"worst": rad, "reasons": reasons or {}},
                           "mmod": {"worst": "acceptable", "reasons": {}}}}


RESULT = {
    "mode": "replay", "as_of": "2024-06-08T03:00:00Z", "search": {"duration_min": 390},
    "recommendation": {"status": "preferred", "window": "W2", "confidence": "low"}, "planned": "W1",
    "windows": [_window("W1", "2024-06-08T06:00:00Z", "2024-06-08T12:30:00Z", "not_recommended", "critical"),
                _window("W2", "2024-06-08T09:00:00Z", "2024-06-08T15:30:00Z", "preferred", "undesirable", {"saa": 20})],
    "sources": [{"source": "goes_protons", "state": "ok"}, {"source": "swpc_3day", "state": "no_data"}],
}


def test_saved_runs_summary_label_delete(tmp_db):
    from services.assessment import api
    with tmp_db.begin() as conn:
        for row in (
            {"id": "r_a", "kind": "assessment", "created_at": utcnow(), "status": "done",
             "request": {"mode": "replay", "as_of": "2024-06-08T03:00:00", "duration_min": 390}, "result": RESULT},
            {"id": "r_v", "kind": "verify", "parent_id": "r_a", "created_at": utcnow(), "status": "done", "request": {}},
            {"id": "r_n", "kind": "assessment", "created_at": utcnow(), "status": "running",
             "request": {"mode": "now", "duration_min": 120}},
        ):
            conn.execute(db.runs.insert().values(**row))
    client = TestClient(api.app)
    rows = client.get("/runs", params={"kind": "assessment"}).json()
    assert {r["run_id"] for r in rows} == {"r_a", "r_n"}
    a = next(r for r in rows if r["run_id"] == "r_a")
    s = a["summary"]
    # старый расчёт без итога: итог посчитан по результату и записан
    assert s["best"]["start"] == "2024-06-08T09:00:00Z" and s["planned"]["status"] == "not_recommended"
    assert s["adverse"] is False  # только ЮАА — фон, вариант не «наименее неблагоприятный»
    assert s["source_issues"] == ["swpc_3day"] and s["counts"] == {"not_recommended": 1, "preferred": 1}
    assert a["verified"] is True
    with tmp_db.connect() as conn:
        assert conn.execute(select(db.runs.c.summary).where(db.runs.c.id == "r_a")).scalar() == s

    assert [r["run_id"] for r in client.get("/runs", params={"kind": "assessment", "mode": "now"}).json()] == ["r_n"]

    assert client.post("/runs/r_a/label", json={"label": "  ВКД-1  "}).json()["label"] == "ВКД-1"
    assert client.get("/runs/r_a", params={"include": "summary"}).json()["label"] == "ВКД-1"
    assert client.post("/runs/nope/label", json={"label": "x"}).status_code == 404

    assert client.delete("/runs/r_n").status_code == 409  # идёт — не удаляем
    assert client.delete("/runs/r_a").json()["deleted"] == 2  # вместе со сверкой
    with tmp_db.connect() as conn:
        assert [r.id for r in conn.execute(select(db.runs.c.id))] == ["r_n"]


def test_alerts_newest_filters_ack_all(tmp_db):
    from services.assessment import api
    now = utcnow()
    with tmp_db.begin() as conn:
        conn.execute(db.alerts.insert(), [
            {"watch_id": w, "created_at": now, "as_of": now, "severity": sev, "kind": "k", "message": sev}
            for w, sev in (("w1", "info"), ("w1", "warning"), ("w2", "critical"), ("w2", "warning"))])
    client = TestClient(api.app)
    assert [a["id"] for a in client.get("/alerts", params={"newest": 1, "limit": 2}).json()] == [4, 3]
    assert [a["id"] for a in client.get("/alerts", params={"severity": "warning"}).json()] == [2, 4]
    assert client.post("/alerts/ack-all", params={"watch_id": "w2"}).json()["acknowledged"] == 2
    assert client.post("/alerts/ack-all").json()["acknowledged"] == 1  # информационные не принимаются
    assert [a["id"] for a in client.get("/alerts", params={"unacked": 1}).json()] == [1]


def test_refresh_all_skips_paused_and_reports_state(tmp_db, monkeypatch):
    from services.ingest import api, registry
    from services.ingest.store import set_paused

    release = threading.Event()
    calls = []

    def fake_run_live(engine, name, *, force=False):
        calls.append((name, force))
        release.wait(5)
        return IngestResult(name, files=1, new_items=3) if name != "kp_observed" else IngestResult(name, errors=["HTTP 503"])

    monkeypatch.setattr(registry, "run_live", fake_run_live)
    monkeypatch.setattr(api, "REFRESH", {})
    with tmp_db.begin() as conn:
        set_paused(conn, "goes_protons", True)
    client = TestClient(api.app)

    out = client.post("/sources/refresh-all").json()
    assert out["skipped_paused"] == ["goes_protons"] and "goes_protons" not in out["started"]
    assert set(out["started"]) == set(registry.ADAPTERS) - {"goes_protons"}
    # повторное нажатие, пока идёт, — не запускает второй раз
    assert client.post("/sources/kp_observed/refresh").json()["already_running"] is True
    running = {s["source"]: s["refresh"] for s in client.get("/sources").json()}
    assert running["kp_observed"]["state"] == "running" and running["goes_protons"] is None

    release.set()
    for _ in range(100):
        state = {s["source"]: s for s in client.get("/sources", params={"detail": 1}).json()}
        if all(s["refresh"] is None or s["refresh"]["state"] != "running" for s in state.values()):
            break
        time.sleep(0.05)
    assert state["swpc_alerts"]["refresh"]["state"] == "done" and state["swpc_alerts"]["refresh"]["new_items"] == 3
    assert state["kp_observed"]["refresh"]["state"] == "error"
    assert state["goes_protons"]["group"] == "radiation" and state["goes_protons"]["provider"]
    assert any(u["title"] == "архив" for u in state["goes_protons"]["urls"])
    assert all(force is False for _, force in calls)  # «обновить все» не трогает замороженные

    # одиночное обновление замороженного — принудительно, один раз
    assert client.post("/sources/goes_protons/refresh").json()["already_running"] is False
    for _ in range(100):
        if ("goes_protons", True) in calls:
            break
        time.sleep(0.05)
    assert ("goes_protons", True) in calls
