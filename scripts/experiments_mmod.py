"""Эксперимент по механизму 2: насколько устойчив прогноз сближений из прошлого.

Для каждого момента отсечки T: прогноз из прошлого (элементы, опубликованные до T) и «факт» — тот же отбор по более
поздним элементам (режим разбора; эпоха элементов — не позже середины участка, поэтому факт считается двумя кусками:
сближения до T+16 ч и после). Сравниваются проходы через зону контроля (стоп-факторы):
- по событиям: подтвердилось (тот же объект, TCA в пределах ±10 мин), ложное, пропущенное — по заблаговременности
  и по возрасту элементов объекта на момент прогноза;
- по окнам: доля окон, закрытых сближением в прогнозе и в факте, и было ли рекомендованное окно закрыто на деле.

python -m scripts.experiments_mmod [--step-h 12] [--out examples/experiments]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from common.timeutil import iso, parse_iso
from services.assessment.runner import RunError, execute
from services.assessment.schemas import RunRequest

PERIOD = (datetime(2024, 5, 1), datetime(2024, 6, 30, 12))
LEAD_BINS = [(0, 6), (6, 12), (12, 24), (24, 32)]
AGE_BINS = [(0, 1), (1, 2), (2, 3.5)]
MATCH_MIN = 10
SPLIT_H = 16


def _box(res: dict, t_from: datetime, t_to: datetime) -> list[dict]:
    return [e for e in res["events"]["mmod"] if e["in_control_box"] and t_from < parse_iso(e["tca"]) <= t_to]


def _match(a: dict, events: list[dict]) -> bool:
    ta = parse_iso(a["tca"])
    return any(e["norad_id"] == a["norad_id"] and abs((parse_iso(e["tca"]) - ta).total_seconds()) <= MATCH_MIN * 60
               for e in events)


def _blocked(w: dict, events: list[dict]) -> bool:
    s, e = parse_iso(w["start"]), parse_iso(w["end"])
    pad = timedelta(minutes=10)
    return any(s - pad <= parse_iso(x["tca"]) < e + pad for x in events)


def one(T: datetime) -> dict:
    base = dict(duration_min=390, mechanisms=["mmod"])
    pred = execute(RunRequest(mode="replay", as_of=T, **base))
    end = parse_iso(pred["search"]["latest_start"]) + timedelta(minutes=390 + 60)
    split = T + timedelta(hours=SPLIT_H)
    act_a = execute(RunRequest(mode="review", as_of=T, earliest_start=T, latest_start=T + timedelta(hours=24), **base))
    act_b = execute(RunRequest(mode="review", as_of=split, earliest_start=split,
                               latest_start=split + timedelta(hours=8), **base))
    p = _box(pred, T, end)
    a = _box(act_a, T, split) + _box(act_b, split, end)
    events = []
    for e in p:
        events.append({"kind": "predicted", "norad_id": e["norad_id"], "object": e["object_name"], "tca": e["tca"],
                       "lead_h": round((parse_iso(e["tca"]) - T).total_seconds() / 3600, 2),
                       "element_age_at_T_d": round((T - parse_iso(e["element_epoch"])).total_seconds() / 86400, 2),
                       "confirmed": _match(e, a), "rel_speed_km_s": e["rel_speed_km_s"]})
    for e in a:
        if not _match(e, p):
            events.append({"kind": "missed", "norad_id": e["norad_id"], "object": e["object_name"], "tca": e["tca"],
                           "lead_h": round((parse_iso(e["tca"]) - T).total_seconds() / 3600, 2)})
    wins = pred["windows"]
    rec = pred["recommendation"].get("window")
    rw = next((w for w in wins if w["id"] == rec), None)
    return {"as_of": iso(T), "events": events, "windows": len(wins),
            "blocked_pred": sum(1 for w in wins if _blocked(w, p)),
            "blocked_actual": sum(1 for w in wins if _blocked(w, a)),
            "blocked_both": sum(1 for w in wins if _blocked(w, p) and _blocked(w, a)),
            "recommended": rec, "recommended_blocked_actual": bool(rw and _blocked(rw, a))}


def summarize(results: list[dict]) -> tuple[dict, str]:
    ev = [e for r in results for e in r["events"]]
    pred = [e for e in ev if e["kind"] == "predicted"]
    miss = [e for e in ev if e["kind"] == "missed"]

    def row(sel_p, sel_m):
        n, ok = len(sel_p), sum(e["confirmed"] for e in sel_p)
        m = len(sel_m)
        return {"predicted": n, "confirmed": ok, "false": n - ok, "missed": m,
                "confirmed_share": round(ok / n, 3) if n else None,
                "detected_share": round(ok / (ok + m), 3) if ok + m else None}

    by_lead = {f"{a}–{b} ч": row([e for e in pred if a <= e["lead_h"] < b], [e for e in miss if a <= e["lead_h"] < b])
               for a, b in LEAD_BINS}
    by_age = {f"{a}–{b} сут": row([e for e in pred if a <= e["element_age_at_T_d"] < b], [])
              for a, b in AGE_BINS}
    total = row(pred, miss)
    wins = {k: sum(r[k] for r in results) for k in ("windows", "blocked_pred", "blocked_actual", "blocked_both")}
    rec_n = sum(1 for r in results if r["recommended"])
    rec_bad = sum(1 for r in results if r["recommended_blocked_actual"])
    summary = {"cutoffs": len(results), "total": total, "by_lead": by_lead, "by_element_age": by_age,
               "windows": wins, "recommended": rec_n, "recommended_blocked_actual": rec_bad}
    lines = [f"Отсечек: {len(results)} ({results[0]['as_of']} … {results[-1]['as_of']}), поиск 24 ч, окно 6.5 ч.", "",
             "| Заблаговременность | Спрогнозировано | Подтвердилось | Ложных | Пропущено | Доля подтверждённых | Доля найденных |",
             "|---|---|---|---|---|---|---|"]
    for k, v in {**by_lead, "всего": total}.items():
        lines.append(f"| {k} | {v['predicted']} | {v['confirmed']} | {v['false']} | {v['missed']} | "
                     f"{v['confirmed_share']} | {v['detected_share']} |")
    lines += ["", "| Возраст элементов объекта на момент прогноза | Спрогнозировано | Подтвердилось | Доля подтверждённых |",
              "|---|---|---|---|"]
    for k, v in by_age.items():
        lines.append(f"| {k} | {v['predicted']} | {v['confirmed']} | {v['confirmed_share']} |")
    lines += ["", f"Окна: закрыты сближением в прогнозе — {wins['blocked_pred']} из {wins['windows']}, на деле — "
                  f"{wins['blocked_actual']}, в обоих — {wins['blocked_both']}. Рекомендованное окно на деле закрыто "
                  f"сближением: {rec_bad} из {rec_n}.", ""]
    return summary, "\n".join(lines)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--step-h", type=float, default=12)
    p.add_argument("--out", default="examples/experiments")
    a = p.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    results, t0 = [], time.time()
    T = PERIOD[0]
    while T <= PERIOD[1]:
        try:
            results.append(one(T))
        except RunError as e:
            print(f"{T}: {e}", flush=True)
        T += timedelta(hours=a.step_h)
        if len(results) % 10 == 0:
            print(f"{len(results)} отсечек, {time.time() - t0:.0f} с", flush=True)
    summary, table = summarize(results)
    (out / "mmod_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "mmod_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "mmod_summary.md").write_text(table, encoding="utf-8")
    print(table)


if __name__ == "__main__":
    main()
