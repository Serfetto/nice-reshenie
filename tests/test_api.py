"""Проверка входа API оценки: прогноз не выбирает окна, начатые до момента расчёта."""
from datetime import timedelta

from fastapi.testclient import TestClient

from common.timeutil import utcnow

from services.assessment import api


def test_replay_rejects_start_before_cutoff(monkeypatch):
    submitted = []
    monkeypatch.setattr(api.jobs, "submit_assessment", lambda req: submitted.append(req) or "r_test")
    client = TestClient(api.app)  # без with: lifespan (БД, наблюдатель) не запускается
    base = {"mode": "replay", "as_of": "2024-06-08T03:00:00Z", "duration_min": 120}
    r = client.post("/runs", json={**base, "earliest_start": "2024-06-08T00:00:00Z"})
    assert r.status_code == 422 and "раньше момента расчёта" in r.json()["detail"]
    assert client.post("/runs", json={**base, "planned_start": "2024-06-08T02:00:00Z"}).status_code == 422
    assert client.post("/runs", json={**base, "mode": "review", "earliest_start": "2024-06-08T00:00:00Z"}).status_code == 202
    assert client.post("/runs", json={**base, "planned_start": "2024-06-08T06:00:00Z"}).status_code == 202
    assert len(submitted) == 2


def test_past_modes_reject_future_moment(monkeypatch):
    monkeypatch.setattr(api.jobs, "submit_assessment", lambda req: "r_test")
    client = TestClient(api.app)
    future = (utcnow() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for mode in ("replay", "review"):
        r = client.post("/runs", json={"mode": mode, "as_of": future, "duration_min": 120})
        assert r.status_code == 422 and "ещё не наступил" in r.json()["detail"]
    past = (utcnow() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert client.post("/runs", json={"mode": "replay", "as_of": past, "duration_min": 120}).status_code == 202


def test_msk_display_time():
    from datetime import datetime

    from common.timeutil import msk
    assert msk("2024-06-08T03:00:00Z") == "06:00"
    assert msk("2024-05-10T21:00:00Z", "%d.%m %H:%M") == "11.05 00:00"  # через полночь
    assert msk(datetime(2024, 6, 8, 3, 0)) == "06:00"  # naive = UTC
    assert msk(None) == "—"
