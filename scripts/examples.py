"""Примеры сохранённых расчётов (examples/): те же выгрузки, что кнопка «Сохранить (ZIP)» в консоли.

Каждый ZIP: report.html и brief.html (читаемо), run.json (запрос, отсечка, оценки, рекомендация, доказательства,
версия алгоритма и настроек, манифест сырых файлов), CSV рядов, окон и интервалов, verification.json (сверка с фактом
для прогноза из прошлого).

python -m scripts.examples [--out examples] [--now]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from common.timeutil import iso, utcnow
from services.assessment.runner import execute, jsonable
from services.assessment.schemas import RunRequest
from services.assessment.verify import verify
from services.report.app import build_zip
from services.report.render import REC_TEXT

CONF = {"high": "высокая", "medium": "средняя", "low": "низкая", "none": "нет"}

CASES = [
    ("replay_20240608_0300", "Прогноз из прошлого 08.06.2024 03:00 — протонное событие только началось (позже дошло до S3); "
     "плановое начало 06:00; сверка с фактом",
     dict(mode="replay", as_of=datetime(2024, 6, 8, 3), duration_min=390, planned_start=datetime(2024, 6, 8, 6)), True),
    ("review_20240511", "Разбор 11.05.2024 по полному архиву — буря G5 и протонное событие S2: открытые участки орбиты",
     dict(mode="review", as_of=datetime(2024, 5, 11), duration_min=390), False),
    ("replay_20240526_conjunction", "Спокойный день 26.05.2024: плановое окно 00:45 закрыто сближением "
     "(встречный пролёт 14 км/с), рекомендовано другое", dict(mode="replay", as_of=datetime(2024, 5, 26), duration_min=390,
                                                              planned_start=datetime(2024, 5, 26, 0, 45)), True),
    ("replay_20240526_goes_disabled", "Тот же спокойный день 26.05.2024 с отключённым источником протонов — "
     "«недостаточно оснований», а не «благоприятно»", dict(mode="replay", as_of=datetime(2024, 5, 26), duration_min=390,
                                                        source_overrides={"goes_protons": {"state": "disabled"}}), False),
    ("replay_20241009_heldout", "Отложенное событие 09.10.2024 06:00 (вне периода отладки): протоны S3 и буря G4",
     dict(mode="replay", as_of=datetime(2024, 10, 9, 6), duration_min=390), True),
]


def _run(name: str, kw: dict, with_verify: bool) -> tuple[dict, dict | None]:
    req = RunRequest(**kw)
    res = execute(req)
    run_id = f"example_{name}"
    res["run_id"] = run_id
    run = {"run_id": run_id, "kind": "assessment", "parent_id": None, "status": "done", "created_at": iso(utcnow()),
           "finished_at": iso(utcnow()), "request": req.model_dump(mode="json"), "result": res, "error": None,
           "algorithm_version": res["algorithm_version"]}
    ver = None
    if with_verify:
        v = jsonable(verify(res, run["request"]))
        ver = {"run_id": f"{run_id}_verify", "kind": "verify", "parent_id": run_id, "status": "done", "result": v}
    return run, ver


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="examples")
    p.add_argument("--now", action="store_true", help="добавить расчёт по текущим данным (нужны свежие данные в БД)")
    a = p.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cases = CASES + ([("now", "Текущая обстановка на момент генерации примеров, окно 4 ч в ближайшие сутки",
                       dict(mode="now", duration_min=240), False)] if a.now else [])
    rows = []
    for name, title, kw, with_verify in cases:
        run, ver = _run(name, kw, with_verify)
        (out / f"{name}.zip").write_bytes(build_zip(run, ver))
        res, rec = run["result"], run["result"]["recommendation"]
        best = next((w for w in res["windows"] if w["id"] == rec.get("window")), None)
        verdict = ""
        if ver:
            rc = ver["result"].get("recommended_window_check") or {}
            verdict = ("на деле в рекомендованном окне был стоп-фактор" if (rc.get("actual_critical_min") or 0) > 0
                       else "на деле стоп-факторов в рекомендованном окне не было") if rc else "рекомендации не было"
        when = f" — {best['start'][11:16]}–{best['end'][11:16]} UTC" if best else ""
        rows.append(f"| [{name}.zip]({name}.zip) | {title} | {REC_TEXT.get(rec['status'], rec['status'])}{when}, "
                    f"уверенность {CONF.get(rec.get('confidence'), rec.get('confidence'))} | {verdict or '—'} |")
        print(name, rec["status"], rec.get("window"), verdict)
    text = ["# Примеры сохранённых расчётов", "",
            "Сгенерированы `python -m scripts.examples` (алгоритм "
            f"{run['algorithm_version']}, {iso(utcnow())}). Каждый ZIP — та же выгрузка, что кнопка «Сохранить (ZIP)» в "
            "консоли: `report.html`, `brief.html`, `run.json` (запрос, момент отсечки, оценки по механизмам, "
            "рекомендация, доказательства со ссылками на сырые файлы `raw_id`, версия алгоритма и хэши настроек), "
            "`series.csv`, `windows.csv`, `intervals.csv`, для прогноза из прошлого — `verification.json` (сверка с фактом).",
            "", "| Файл | Что показывает | Рекомендация | Сверка с фактом |", "|---|---|---|---|", *rows, "",
            "Результаты эксперимента (прогноз из прошлого на событиях и спокойных периодах, три модели) — "
            "[experiments/](experiments/), выводы — [docs/experiments.md](../docs/experiments.md).", ""]
    (out / "README.md").write_text("\n".join(text), encoding="utf-8")


if __name__ == "__main__":
    main()
