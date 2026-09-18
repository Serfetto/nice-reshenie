"""Временная честность: срез не видит данных, опубликованных после отсечки."""
from datetime import datetime, timedelta

import pytest

from common.db import get_engine, init_db, insert_ignore, messages, records
from services.assessment.slice import DataSlice, Override

T = datetime(2024, 6, 8, 3, 0)


@pytest.fixture()
def engine(tmp_path):
    eng = get_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    init_db(eng)
    rows = []
    for k in range(12):  # наблюдения 02:00..02:55 и 03:00..03:55
        t0 = datetime(2024, 6, 8, 2, 0) + timedelta(minutes=5 * k * 2)
        rows.append(dict(source="goes_protons", product="p", quantity="p_ge10", unit="pfu", value=float(k),
                         valid_from=t0, valid_to=t0 + timedelta(minutes=5), issued_at=t0 + timedelta(minutes=5),
                         issued_at_policy="estimated", fetched_at=t0, kind="observation", region="geo"))
    # прогноз, выпущенный до и после отсечки на один и тот же интервал
    for issued, val in ((T - timedelta(hours=2), 3.0), (T + timedelta(hours=1), 7.0)):
        rows.append(dict(source="swpc_3day", product="3day", quantity="kp", unit="", value=val,
                         valid_from=datetime(2024, 6, 8, 6), valid_to=datetime(2024, 6, 8, 9), issued_at=issued,
                         issued_at_policy="exact", fetched_at=issued, kind="forecast", region="planetary"))
    with eng.begin() as c:
        insert_ignore(c, records, rows)
        insert_ignore(c, messages, [
            dict(source="swpc_alerts", code="WARPX1", serial=1, issued_at=T - timedelta(minutes=20),
                 fetched_at=T, msg_type="WARNING"),
            dict(source="swpc_alerts", code="ALTPX1", serial=2, issued_at=T + timedelta(minutes=5),
                 fetched_at=T, msg_type="ALERT")])
    return eng


def test_replay_excludes_future(engine):
    sl = DataSlice(engine, "replay", T)
    obs = sl.records("goes_protons", "p_ge10", T - timedelta(hours=2), T + timedelta(hours=2), "observation")
    assert obs and all(r.issued_at <= T for r in obs)
    fc = sl.latest_forecasts(["swpc_3day"], "kp", T, T + timedelta(hours=12))
    assert len(fc) == 1 and fc[0].value == 3.0  # выпуск после T не виден
    msgs = sl.messages(("WARPX", "ALTPX"), T - timedelta(days=1))
    assert [m.code for m in msgs] == ["WARPX1"]
    m = sl.manifest()
    assert m["swpc_3day"]["excluded_after_cutoff"] >= 1
    assert m["swpc_alerts"]["excluded_after_cutoff"] >= 1


def test_review_sees_everything(engine):
    sl = DataSlice(engine, "review", T)
    fc = sl.latest_forecasts(["swpc_3day"], "kp", T, T + timedelta(hours=12))
    assert fc[0].value == 7.0  # в разборе виден самый свежий выпуск


def test_disabled_and_frozen(engine):
    sl = DataSlice(engine, "replay", T, {"goes_protons": Override("disabled")})
    assert sl.records("goes_protons", "p_ge10", T - timedelta(hours=2), T, "observation") == []
    assert sl.freshness("goes_protons")["state"] == "disabled"
    frozen_at = datetime(2024, 6, 8, 2, 30)
    sl = DataSlice(engine, "replay", T, {"goes_protons": Override("frozen", frozen_at)})
    obs = sl.records("goes_protons", "p_ge10", T - timedelta(hours=2), T, "observation")
    assert max(r.issued_at for r in obs) <= frozen_at
