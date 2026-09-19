"""Правило выбора окна на синтетических шкалах."""
from datetime import datetime, timedelta

import numpy as np

from common import config
from common.timeutil import time_grid, to_np
from services.assessment.core.timeline import ACCEPTABLE, CRITICAL, NO_DATA, UNDESIRABLE, MechanismTimeline
from services.assessment.core.windows import evaluate_windows

G0 = datetime(2024, 6, 1)


def mech(name: str, spans: list[tuple], hours: int = 20) -> MechanismTimeline:
    grid = time_grid(G0, G0 + timedelta(hours=hours), 60)
    n = len(grid)
    cls = np.full(n, ACCEPTABLE, dtype=np.int8)
    reason = np.full(n, "x", dtype=object)
    for a, b, c, *r in spans:  # минуты от начала, класс, [причина]
        m = (grid >= to_np(G0 + timedelta(minutes=a))) & (grid < to_np(G0 + timedelta(minutes=b)))
        cls[m] = c
        if r:
            reason[m] = r[0]
    return MechanismTimeline(name, grid, cls, reason, np.full(n, "observation", dtype=object),
                             np.full(n, "high", dtype=object), [()] * n)


def run(mechs, earliest=0, latest=240, duration=120, planned=None):
    cfg = config.thresholds()
    return evaluate_windows(mechs, G0 + timedelta(minutes=earliest), G0 + timedelta(minutes=latest), duration,
                            None if planned is None else G0 + timedelta(minutes=planned), 30,
                            {**cfg, "window": {**cfg["window"], "step_min": 60}})


def status(ev, start_min):
    t = (G0 + timedelta(minutes=start_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return next(w for w in ev["windows"] if w["start"] == t)


def test_critical_excludes_window():
    ev = run({"radiation": mech("radiation", [(30, 40, CRITICAL)])})
    assert status(ev, 0)["status"] == "not_recommended" and status(ev, 0)["rule"] == "R1"
    assert ev["recommendation"]["window"] != status(ev, 0)["id"]


def test_no_data_requires_review_and_never_favorable():
    ev = run({"radiation": mech("radiation", [(0, 600, NO_DATA)])})
    assert all(w["status"] == "requires_review" for w in ev["windows"])
    assert ev["recommendation"]["status"] == "insufficient_basis"


def test_all_critical_gives_no_window():
    ev = run({"radiation": mech("radiation", [(0, 1200, CRITICAL)])})
    assert ev["recommendation"]["status"] == "no_window"


def test_fewer_undesirable_minutes_wins():
    ev = run({"radiation": mech("radiation", [(0, 60, UNDESIRABLE)])})
    best = ev["recommendation"]["window"]
    assert best != status(ev, 0)["id"]
    assert status(ev, 0)["status"] == "worse"


def test_identical_windows_are_equivalent():
    ev = run({"radiation": mech("radiation", [])})
    assert ev["recommendation"]["status"] == "equivalent"


def test_overrun_margin_detected():
    ev = run({"radiation": mech("radiation", [(130, 140, CRITICAL)])}, planned=0)
    w = status(ev, 0)
    assert w["status"] != "not_recommended"
    assert w["overrun"]["first_critical_after_end_min"] == 10


def test_mechanisms_not_compensated():
    # по радиации окно чистое, но по сближениям критично -> окно не рекомендуется
    ev = run({"radiation": mech("radiation", []), "mmod": mech("mmod", [(10, 20, CRITICAL)])})
    assert status(ev, 0)["status"] == "not_recommended"


def test_meteor_statistics_ranked_after_radiation():
    # окно с 0: 30 мин нежелательно по радиации; окна с 60 и 120: по 60 мин метеорного потока — они лучше
    ev = run({"radiation": mech("radiation", [(0, 30, UNDESIRABLE)]),
              "mmod": mech("mmod", [(120, 180, UNDESIRABLE, "meteor_shower")])}, latest=120)
    assert status(ev, 0)["status"] == "worse"
    assert ev["recommendation"]["window"] in (status(ev, 60)["id"], status(ev, 120)["id"])
