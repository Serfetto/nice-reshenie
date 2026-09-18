"""Окна ВКД одинаковой длительности и правило выбора.

Правило (механизмы не складываются в одно число):
R1  в окне есть критический фактор по любому механизму      -> «не рекомендуется»
R2  в окне есть интервалы без данных                         -> «требует проверки»
R3  среди остальных: окно не хуже другого по всем механизмам и лучше хотя бы по одному
    (минуты нежелательных условий) — лучше (Парето)
R4  несравнимые окна: приоритет механизмов из конфига, компромисс показывается явно
R5  при равенстве — больший запас до первого критического фактора после окончания (задержка работ)
R6  разница в пределах допуска — «равнозначно»
R7  нет окон без критических факторов / без пропусков — «нет подходящего окна» / «недостаточно оснований»
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

import numpy as np

from common.timeutil import from_np, iso, to_np
from services.assessment.core.timeline import (ACCEPTABLE, CLASS_NAMES, CRITICAL, NO_DATA, UNDESIRABLE,
                                               MechanismTimeline, min_confidence)


def _window_stats(mech: MechanismTimeline, mask: np.ndarray, step_min: float) -> dict:
    cls = mech.cls[mask]
    minutes = {CLASS_NAMES[k]: round(float(np.sum(cls == k)) * step_min, 1)
               for k in (CRITICAL, UNDESIRABLE, ACCEPTABLE, NO_DATA)}
    reasons = Counter()
    for c, r in zip(cls, mech.reason[mask]):
        if c != ACCEPTABLE:
            reasons[r] += step_min
    worst = "critical" if minutes["critical"] else "no_data" if minutes["no_data"] else \
        "undesirable" if minutes["undesirable"] else "acceptable"
    known = mech.confidence[mask][cls != NO_DATA]
    out = {"minutes": minutes, "worst": worst, "confidence": min_confidence(known),
           "reasons": {k: round(v, 1) for k, v in reasons.most_common()}}
    if "j_iss" in mech.series:
        # оценка потока выше порога обрезания, накопленного за окно (pfu·мин) — мера для различения окон;
        # минуты, где поток не оценён количественно (только вероятность), считаются отдельно и не дают «нуля»
        j = mech.series["j_iss"][mask]
        out["exposure_pfu_min"] = round(float(np.nansum(j)) * step_min, 2)
        out["unassessed_min"] = round(float(np.sum(~np.isfinite(j) & (cls != NO_DATA))) * step_min, 1)
    return out


def evaluate_windows(mechs: dict[str, MechanismTimeline], earliest: datetime, latest: datetime, duration_min: int,
                     planned_start: datetime | None, margin_min: int, cfg: dict) -> dict:
    wc = cfg["window"]
    any_mech = next(iter(mechs.values()))
    grid = any_mech.times
    step_s = int((grid[1] - grid[0]) / np.timedelta64(1, "s"))
    step_min = step_s / 60.0
    starts, t = [], earliest
    while t <= latest:
        starts.append(t)
        t += timedelta(minutes=wc["step_min"])
    if planned_start is not None and planned_start not in starts:
        starts.append(planned_start)
        starts.sort()

    windows = []
    for k, s in enumerate(starts):
        e = s + timedelta(minutes=duration_min)
        mask = (grid >= to_np(s)) & (grid < to_np(e))
        after = (grid >= to_np(e)) & (grid < to_np(e + timedelta(minutes=margin_min)))
        w = {"id": f"W{k + 1}", "start": iso(s), "end": iso(e), "is_planned": planned_start == s,
             "mechanisms": {name: _window_stats(m, mask, step_min) for name, m in mechs.items()}}
        first_crit = None
        for name, m in mechs.items():
            idx = np.nonzero(after & (m.cls == CRITICAL))[0]
            if len(idx):
                mins = float((grid[idx[0]] - to_np(e)) / np.timedelta64(1, "m"))
                first_crit = mins if first_crit is None else min(first_crit, mins)
        w["overrun"] = {"margin_min": margin_min, "first_critical_after_end_min": first_crit,
                        "robust": first_crit is None}
        crit = any(v["minutes"]["critical"] > 0 for v in w["mechanisms"].values())
        nodata = any(v["minutes"]["no_data"] > 0 for v in w["mechanisms"].values())
        if crit:
            w["status"], w["rule"] = "not_recommended", "R1"
        elif nodata:
            w["status"], w["rule"] = "requires_review", "R2"
        else:
            w["status"], w["rule"] = "candidate", None
        w["confidence"] = min_confidence([v["confidence"] for v in w["mechanisms"].values()])
        windows.append(w)

    priority = [p for p in wc["priority"] if p in mechs] + [p for p in mechs if p not in wc["priority"]]
    tol = wc["tolerance_min"]
    cands = [w for w in windows if w["status"] == "candidate"]

    def vec(w):
        return [w["mechanisms"][p]["minutes"]["undesirable"] for p in priority]

    def robust(w):
        f = w["overrun"]["first_critical_after_end_min"]
        return margin_min + 1 if f is None else f

    def expo(w):
        return w["mechanisms"].get("radiation", {}).get("exposure_pfu_min", 0.0)

    def unassessed(w):
        return w["mechanisms"].get("radiation", {}).get("unassessed_min", 0.0)

    rel_tol = cfg.get("radiation", {}).get("exposure_rel_tolerance", 0.2)

    recommendation: dict
    if cands:
        pareto = [w for w in cands if not any(
            all(a <= b for a, b in zip(vec(o), vec(w))) and any(a < b for a, b in zip(vec(o), vec(w)))
            for o in cands if o is not w)]
        # R3/R4: минуты нежелательных условий по механизмам в порядке приоритета;
        # затем меньше минут без количественной оценки потока (неизвестное не считается благоприятным);
        # затем оценка потока выше порога обрезания за окно; R5: затем запас на задержку
        best = min(pareto, key=lambda w: (*vec(w), unassessed(w), expo(w), -robust(w), w["start"]))
        bv = vec(best)
        expo_tol = rel_tol * max(expo(best), 1.0)
        equivalents = [w for w in cands if w is not best and all(abs(a - b) <= tol for a, b in zip(vec(w), bv))
                       and abs(unassessed(w) - unassessed(best)) <= tol
                       and abs(expo(w) - expo(best)) <= expo_tol
                       and (robust(w) >= robust(best) or w["overrun"]["robust"] == best["overrun"]["robust"])]
        for w in cands:
            w["status"], w["rule"] = "worse", "R3"
        best["status"], best["rule"] = ("equivalent", "R6") if equivalents else ("preferred", "R3")
        for w in equivalents:
            w["status"], w["rule"] = "equivalent", "R6"
        tradeoffs = []
        for w in pareto:
            if w is best or w in equivalents:
                continue
            better = [p for p, a, b in zip(priority, vec(w), bv) if a < b - tol]
            worse = [p for p, a, b in zip(priority, vec(w), bv) if a > b + tol]
            if better:
                w["rule"] = "R4"
                tradeoffs.append({"window": w["id"], "better_in": better, "worse_in": worse,
                                  "resolved_by": f"приоритет механизмов: {' > '.join(priority)}"})
        recommendation = {
            "status": "equivalent" if equivalents else "preferred",
            "window": best["id"], "equivalent_windows": [w["id"] for w in equivalents][:20],
            "n_equivalent": len(equivalents),
            "rule": best["rule"], "confidence": best["confidence"], "tentative": best["confidence"] == "low",
            "tradeoffs": tradeoffs[:10]}
    else:
        if any(w["status"] == "requires_review" for w in windows):
            recommendation = {"status": "insufficient_basis", "window": None, "rule": "R7",
                              "confidence": "none", "tradeoffs": []}
        else:
            recommendation = {"status": "no_window", "window": None, "rule": "R7", "confidence": "none",
                              "tradeoffs": []}
    planned = next((w for w in windows if w["is_planned"]), None)
    return {"windows": windows, "recommendation": recommendation, "planned": planned["id"] if planned else None,
            "priority": priority, "step_min": wc["step_min"], "tolerance_min": tol}
