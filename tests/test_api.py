"""Проверка входа API оценки: прогноз не выбирает окна, начатые до момента расчёта."""
from fastapi.testclient import TestClient

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
