"""Проверки корректности данных и расчёта (для отчёта и регрессии).

1. Полнота архива за май–июнь 2024 по источникам.
2. Реконструкция GOES против официальных сводок SWPC о протонных событиях (начало и пик).
3. Сценарии replay: ожидаемое поведение классов и честность среза по времени.

python -m scripts.validate [--out data/validation]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from common.db import get_engine
from services.assessment.runner import execute
from services.assessment.schemas import RunRequest

P0, P1 = datetime(2024, 5, 1), datetime(2024, 7, 1)


def coverage(conn) -> dict:
    q = lambda sql: [dict(r._mapping) for r in conn.execute(text(sql), {"a": P0, "b": P1})]
    return {
        "goes_p10_per_day": q("select substr(cast(valid_from as text),1,10) d, count(*) n from records where quantity='p_ge10' "
                              "and product='ncei_sgps_l2_integrated' and valid_from>=:a and valid_from<:b group by 1"),
        "kp_obs_days": q("select count(distinct substr(cast(valid_from as text),1,10)) n from records where source='kp_observed' "
                         "and valid_from>=:a and valid_from<:b"),
        "three_day_issues": q("select count(distinct issued_at) n from records where source='swpc_3day' "
                              "and issued_at>=:a and issued_at<:b"),
        "rsga_issues": q("select count(distinct issued_at) n from records where source='swpc_rsga' "
                         "and issued_at>=:a and issued_at<:b"),
        "alerts": q("select count(*) n from messages where issued_at>=:a and issued_at<:b"),
        "iss_elements": q("select count(*) n from elements where norad_id=25544 and creation_date>=:a and creation_date<:b"),
        "catalog_objects_per_day": q("select substr(cast(epoch as text),1,10) d, count(distinct norad_id) n from elements "
                                     "where norad_id<>25544 and source='spacetrack' and epoch>=:a and epoch<:b group by 1"),
    }


def goes_vs_swpc(conn) -> list[dict]:
    """Сравнение восстановленного ряда ≥10 МэВ со сводками SWPC (SUMPX1: начало, пик)."""
    out = []
    sums = conn.execute(text("select code, begin_time, max_time, end_time, max_flux from messages "
                             "where code='SUMPX1' and issued_at>=:a and issued_at<:b order by begin_time"),
                        {"a": P0, "b": P1}).fetchall()
    for s in sums:
        b = datetime.fromisoformat(str(s.begin_time))
        rows = conn.execute(text("select valid_from, value from records where quantity='p_ge10' and "
                                 "product='ncei_sgps_l2_integrated' and valid_from>=:t0 and valid_from<=:t1 "
                                 "order by valid_from"),
                            {"t0": b - timedelta(hours=3), "t1": datetime.fromisoformat(str(s.end_time))}).fetchall()
        first = next((r.valid_from for r in rows if r.value >= 10), None)
        peak = max(rows, key=lambda r: r.value) if rows else None
        out.append({"swpc_begin": str(s.begin_time), "reconstructed_first_ge10": str(first),
                    "swpc_max_time": str(s.max_time), "swpc_max_pfu": s.max_flux,
                    "reconstructed_max_time": str(peak.valid_from) if peak else None,
                    "reconstructed_max_pfu": round(peak.value, 1) if peak else None})
    return out


SCENARIOS = [
    ("до события 10.05, буря ожидается", dict(mode="replay", as_of="2024-05-10T12:00:00Z")),
    ("событие S2 + буря G4–G5", dict(mode="replay", as_of="2024-05-10T21:00:00Z")),
    ("нарастание события 08.06", dict(mode="replay", as_of="2024-06-08T03:00:00Z",
                                      planned_start="2024-06-08T06:00:00Z")),
    ("пик S3 08.06", dict(mode="replay", as_of="2024-06-08T09:00:00Z")),
    ("спокойный период", dict(mode="replay", as_of="2024-05-26T00:00:00Z")),
    ("GOES отключён", dict(mode="replay", as_of="2024-05-26T00:00:00Z",
                           source_overrides={"goes_protons": {"state": "disabled"}})),
    ("GOES заморожен за 2 ч до T", dict(mode="replay", as_of="2024-05-26T00:00:00Z",
                                        source_overrides={"goes_protons": {"state": "frozen",
                                                                           "at": "2024-05-25T22:00:00Z"}})),
    ("каталог отключён", dict(mode="replay", as_of="2024-05-26T00:00:00Z",
                              source_overrides={"catalog_spacetrack": {"state": "disabled"}})),
    ("разбор 11.05 (весь архив)", dict(mode="review", as_of="2024-05-11T00:00:00Z")),
]


def scenario_summary(res: dict) -> dict:
    from collections import Counter
    rad = res["timeline"].get("radiation", [])
    reasons = Counter()
    for iv in rad:
        t0, t1 = datetime.fromisoformat(iv["from"][:-1]), datetime.fromisoformat(iv["to"][:-1])
        if t0 >= datetime.fromisoformat(res["as_of"][:-1]):
            reasons[f"{iv['class']}:{iv['reason']}"] += round((t1 - t0).total_seconds() / 60)
    leak = {s: m["latest_issued"] for s, m in res["manifest"].items()
            if m["latest_issued"] and res["mode"] == "replay" and m["latest_issued"] > res["as_of"]}
    return {
        "recommendation": {k: res["recommendation"].get(k) for k in ("status", "window", "rule", "confidence")},
        "window_statuses": dict(Counter(w["status"] for w in res["windows"])),
        "radiation_minutes_after_T": dict(reasons.most_common()),
        "conjunctions_in_box": sum(1 for e in res["events"].get("mmod", []) if e["in_control_box"]),
        "conjunctions_reported": len(res["events"].get("mmod", [])),
        "sources": {s["source"]: s["state"] for s in res["sources"]},
        "excluded_after_cutoff": {s: m["excluded_after_cutoff"] for s, m in res["manifest"].items()
                                  if m["excluded_after_cutoff"]},
        "leak_check": "ok" if not leak else f"ДАННЫЕ ПОСЛЕ ОТСЕЧКИ: {leak}",
        "reconstruction": res["reconstruction"],
        "first_lines": res["explanation"]["text"][:2],
    }


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="data/validation")
    a = p.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with get_engine().connect() as conn:
        report = {"coverage": coverage(conn), "goes_vs_swpc": goes_vs_swpc(conn)}
    report["scenarios"] = {}
    for title, params in SCENARIOS:
        res = execute(RunRequest(duration_min=390, **params))
        report["scenarios"][title] = scenario_summary(res)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
