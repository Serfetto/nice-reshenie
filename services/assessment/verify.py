"""Сверка прогноза из прошлого с тем, что было на самом деле.

Прогноз (replay) не меняется. Тот же запрос пересчитывается в режиме разбора (весь архив),
и результаты сравниваются: по минутам (попадания, пропуски, ложные тревоги) и по окнам.
ЮАА и метеорные потоки исключаются из сравнения: это геометрия и годовой прогноз, они совпадают тривиально.
"""
from __future__ import annotations


import numpy as np

from common.timeutil import parse_iso
from services.assessment.runner import RunError, execute
from services.assessment.schemas import RunRequest

ADVERSE = ("undesirable", "critical")


def _arrays(times: list[str], intervals: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    t = np.array([np.datetime64(x[:-1]) for x in times])
    cls = np.full(len(t), "no_data", dtype=object)
    reason = np.full(len(t), "", dtype=object)
    for iv in intervals:
        m = (t >= np.datetime64(iv["from"][:-1])) & (t < np.datetime64(iv["to"][:-1]))
        cls[m] = iv["class"]
        reason[m] = iv["reason"]
    return cls, reason


def _is_event(cls: np.ndarray, reason: np.ndarray, mech: str, level: tuple[str, ...]) -> np.ndarray:
    hit = np.isin(cls, level)
    if mech == "radiation":
        hit &= np.array([r.startswith("sep_") for r in reason])
    elif mech == "mmod":  # метеорные потоки — заранее известный прогноз, совпадает тривиально
        hit &= reason == "conjunction"
    return hit


def _scores(pred: np.ndarray, act: np.ndarray, valid: np.ndarray) -> dict:
    p, a = pred[valid], act[valid]
    hits, misses = int(np.sum(p & a)), int(np.sum(~p & a))
    fa, cn = int(np.sum(p & ~a)), int(np.sum(~p & ~a))
    return {"hits_min": hits, "misses_min": misses, "false_alarm_min": fa, "correct_negative_min": cn,
            "pod": round(hits / (hits + misses), 3) if hits + misses else None,
            "far": round(fa / (hits + fa), 3) if hits + fa else None}


def verify(parent: dict, parent_request: dict) -> dict:
    if parent.get("mode") != "replay":
        raise RunError("Сверка доступна только для прогноза из прошлого (replay).")
    s = parent["search"]
    req = RunRequest(mode="review", as_of=parse_iso(parent["as_of"]), duration_min=s["duration_min"],
                     earliest_start=parse_iso(s["earliest_start"]), latest_start=parse_iso(s["latest_start"]),
                     step_min=s["step_min"], planned_start=parse_iso(parent_request.get("planned_start")),
                     eva=parent_request.get("eva") or {}, mechanisms=list(parent["timeline"].keys()),
                     radiation_model=parent.get("radiation_model", "team"))
    actual = execute(req)
    as_of = np.datetime64(parent["as_of"][:-1])
    times = parent["series"]["times"]
    if times != actual["series"]["times"]:
        raise RunError("Сетки прогноза и факта не совпали — сверка невозможна.")
    fut = np.array([np.datetime64(x[:-1]) for x in times]) > as_of

    per_mech = {}
    for mech in parent["timeline"]:
        pc, pr = _arrays(times, parent["timeline"][mech])
        ac, ar = _arrays(times, actual["timeline"][mech])
        valid = fut & (pc != "no_data") & (ac != "no_data")
        per_mech[mech] = {
            "adverse": _scores(_is_event(pc, pr, mech, ADVERSE), _is_event(ac, ar, mech, ADVERSE), valid),
            "critical": _scores(_is_event(pc, pr, mech, ("critical",)), _is_event(ac, ar, mech, ("critical",)), valid),
            "compared_min": int(valid.sum()), "forecast_no_data_min": int(np.sum(fut & (pc == "no_data"))),
        }
        act_ev = _is_event(ac, ar, mech, ADVERSE) & fut
        if act_ev.any():
            k = int(np.argmax(act_ev))
            onset = np.datetime64(times[k][:-1])
            predicted = bool(_is_event(pc[k:k + 1], pr[k:k + 1], mech, ADVERSE)[0])
            per_mech[mech]["first_actual_event"] = {
                "time": times[k], "reason": ar[k], "predicted_at_onset": predicted,
                "lead_time_h": round(float((onset - as_of) / np.timedelta64(1, "h")), 2) if predicted else None}

    pw = {w["id"]: w for w in parent["windows"]}
    aw = {w["id"]: w for w in actual["windows"]}
    windows = []
    for wid, w in pw.items():
        a = aw.get(wid)
        if a is None:
            continue
        windows.append({"id": wid, "start": w["start"], "predicted_status": w["status"], "actual_status": a["status"],
                        "predicted_critical_min": sum(m["minutes"]["critical"] for m in w["mechanisms"].values()),
                        "actual_critical_min": sum(m["minutes"]["critical"] for m in a["mechanisms"].values())})
    rec = parent["recommendation"]
    rec_check = None
    if rec.get("window") and rec["window"] in aw:
        a = aw[rec["window"]]
        rec_check = {"window": rec["window"], "actual_status": a["status"],
                     "actual_critical_min": sum(m["minutes"]["critical"] for m in a["mechanisms"].values()),
                     "actual_best_window": actual["recommendation"].get("window")}
    return {"parent_as_of": parent["as_of"], "mechanisms": per_mech, "recommended_window_check": rec_check,
            "windows": windows, "actual_recommendation": actual["recommendation"],
            "actual_explanation": actual["explanation"]["text"][:3],
            "actual_sources": actual["sources"], "note": "Факт — режим разбора по всему архиву (реконструкция). "
            "ЮАА исключена из сравнения. Минуты считаются по сетке результата (60 с)."}
