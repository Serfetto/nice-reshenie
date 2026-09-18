"""Эксперимент: прогноз из прошлого на событиях и спокойном периоде, сравнение с простыми подходами.

Для каждого момента отсечки T выполняется replay (данные только до T) и сверка с фактом (весь архив).
Модели радиационного прогноза:
  team        — собственный прогноз (рост/спад на 6 ч) + предупреждения SWPC + суточные вероятности
  persistence — базовый: последнее наблюдение сохраняется
  swpc_only   — базовый: только готовые предупреждения SWPC, иначе фон
Показатели по минутам после T (ЮАА исключена): попадания, пропуски, ложные тревоги, POD, FAR;
по окнам: было ли в рекомендованном окне критическое условие на самом деле.

python -m scripts.experiments [--quick] [--out data/experiments]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from services.assessment.runner import RunError, execute
from services.assessment.schemas import RunRequest
from services.assessment.verify import verify

CASES = [
    ("sep_0510", "Протонное событие 10–12.05 (S2) + буря G5", datetime(2024, 5, 10, 6), datetime(2024, 5, 11, 12), 3),
    ("sep_0513", "Протонное событие 13–16.05 (S2)", datetime(2024, 5, 13, 6), datetime(2024, 5, 14, 6), 3),
    ("sep_0608", "Протонное событие 08–09.06 (S3)", datetime(2024, 6, 7, 18), datetime(2024, 6, 8, 18), 3),
    ("warn_0612", "Предупреждение 12–13.06 без превышения порога", datetime(2024, 6, 12, 0), datetime(2024, 6, 12, 18), 6),
    ("quiet_0524", "Спокойный период 24–29.05 (контроль)", datetime(2024, 5, 24, 0), datetime(2024, 5, 29, 0), 12),
]
MODELS = ["team", "persistence", "swpc_only"]
KEYS = ("hits_min", "misses_min", "false_alarm_min", "correct_negative_min")


def _agg(rows: list[dict], level: str) -> dict:
    tot = {k: sum(r["scores"][level][k] for r in rows) for k in KEYS}
    h, m, fa = tot["hits_min"], tot["misses_min"], tot["false_alarm_min"]
    tot["pod"] = round(h / (h + m), 3) if h + m else None
    tot["far"] = round(fa / (h + fa), 3) if h + fa else None
    return tot


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--quick", action="store_true", help="по одному моменту на случай")
    p.add_argument("--out", default="data/experiments")
    a = p.parse_args(argv)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    t_start = time.time()
    for case_id, title, t0, t1, step_h in CASES:
        cutoffs = []
        t = t0
        while t <= t1:
            cutoffs.append(t)
            t += timedelta(hours=step_h)
        if a.quick:
            cutoffs = cutoffs[len(cutoffs) // 2:len(cutoffs) // 2 + 1]
        for model in MODELS:
            for T in cutoffs:
                req = RunRequest(mode="replay", as_of=T, duration_min=390, mechanisms=["radiation"],
                                 radiation_model=model)
                try:
                    res = execute(req)
                    v = verify(res, req.model_dump(mode="json"))
                except RunError as e:
                    print(f"  {case_id} {model} {T}: {e}")
                    continue
                rad = v["mechanisms"]["radiation"]
                rc = v["recommended_window_check"] or {}
                results.append({
                    "case": case_id, "model": model, "as_of": T.isoformat() + "Z",
                    "scores": {"adverse": rad["adverse"], "critical": rad["critical"]},
                    "forecast_no_data_min": rad["forecast_no_data_min"],
                    "first_actual_event": rad.get("first_actual_event"),
                    "recommendation": res["recommendation"]["status"],
                    "recommended_window": rc.get("window"),
                    "recommended_actual_status": rc.get("actual_status"),
                    "recommended_actual_critical_min": rc.get("actual_critical_min"),
                })
            print(f"{case_id:10s} {model:12s} готово ({len(cutoffs)} отсечек, {time.time() - t_start:.0f} с)")

    (out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = ["| Случай | Модель | POD (неблаг.) | FAR (неблаг.) | POD (крит.) | FAR (крит.) | "
             "Пропуск, мин | Ложн. тревога, мин | Рекоменд. окно оказалось критическим |",
             "|---|---|---|---|---|---|---|---|---|"]
    summary = {}
    for case_id, title, *_ in CASES:
        for model in MODELS:
            rows = [r for r in results if r["case"] == case_id and r["model"] == model]
            if not rows:
                continue
            adv, crit = _agg(rows, "adverse"), _agg(rows, "critical")
            bad = sum(1 for r in rows if (r["recommended_actual_critical_min"] or 0) > 0)
            n_rec = sum(1 for r in rows if r["recommended_window"])
            summary[f"{case_id}/{model}"] = {"adverse": adv, "critical": crit, "recommended_bad": bad,
                                             "recommended_total": n_rec, "cutoffs": len(rows)}
            lines.append(f"| {title} | {model} | {adv['pod']} | {adv['far']} | {crit['pod']} | {crit['far']} | "
                         f"{adv['misses_min']} | {adv['false_alarm_min']} | {bad} из {n_rec} |")
    table = "\n".join(lines)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "summary.md").write_text(table + "\n", encoding="utf-8")
    print(table)


if __name__ == "__main__":
    main()
