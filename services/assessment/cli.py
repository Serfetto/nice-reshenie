"""Расчёт из командной строки (без API).

python -m services.assessment.cli run --mode replay --as-of 2024-06-08T03:00 --duration 390 [--planned 2024-06-08T06:00]
python -m services.assessment.cli run --mode now --duration 240
Флаг --verify после replay сразу выполняет сверку с фактом. --out сохраняет полный JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from services.assessment.runner import execute
from services.assessment.schemas import RunRequest


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="assessment")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--mode", default="now", choices=["now", "replay", "review"])
    r.add_argument("--as-of")
    r.add_argument("--duration", type=int, default=390, help="минуты")
    r.add_argument("--earliest")
    r.add_argument("--latest")
    r.add_argument("--planned")
    r.add_argument("--model", default="team", choices=["team", "persistence", "swpc_only"])
    r.add_argument("--disable", action="append", default=[], help="отключить источник на этот расчёт")
    r.add_argument("--verify", action="store_true")
    r.add_argument("--out")
    a = p.parse_args(argv)

    req = RunRequest(mode=a.mode, as_of=a.as_of, duration_min=a.duration, earliest_start=a.earliest,
                     latest_start=a.latest, planned_start=a.planned, radiation_model=a.model,
                     source_overrides={s: {"state": "disabled"} for s in a.disable})
    res = execute(req)
    print("\n".join(res["explanation"]["text"]))
    print("Источники:", ", ".join(f"{s['source']}={s['state']}" for s in res["sources"]))
    print("Пересчитать:", "; ".join(f"{x['time']} ({x['reason']})" for x in res["recheck_after"]))
    out = {"result": res}
    if a.verify:
        from services.assessment.verify import verify
        v = verify(res, req.model_dump(mode="json"))
        out["verify"] = v
        print("Сверка:", json.dumps({"mechanisms": v["mechanisms"], "recommended": v["recommended_window_check"]},
                                    ensure_ascii=False, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
