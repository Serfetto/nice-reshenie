"""Текстовые объяснения рекомендации — из тех же фактов, что и расчёт."""
from __future__ import annotations

from services.assessment.core.timeline import REASONS

MECH_TITLES = {"radiation": "Радиация", "mmod": "Мусор и метеороиды"}
STATUS_TEXT = {
    "preferred": "предпочтительно", "equivalent": "равнозначно лучшему", "worse": "допустимо, но хуже",
    "not_recommended": "не рекомендуется (критический фактор)", "requires_review": "требует проверки (нет данных)",
}
CONF_TEXT = {"high": "высокая", "medium": "средняя", "low": "низкая", "none": "нет"}
# правила выбора окна простыми словами (подробно — docs/method.md, §5)
RULE_TEXT = {
    "R1": "в окне есть стоп-фактор — окно не рекомендуется",
    "R2": "в окне есть интервалы без данных — окно требует проверки",
    "R3": "меньше всего минут нежелательных условий: сначала по радиации, затем по сближениям; при равенстве — меньше "
          "минут без количественной оценки потока, меньше поток частиц за окно, меньше минут метеорных потоков "
          "(статистика), больше запас на задержку",
    "R4": "окна несравнимы (одно лучше по одному механизму, другое — по другому) — решено приоритетом: радиация важнее",
    "R6": "разница с лучшим в пределах допуска (5 мин, 20 % потока) — окна равнозначны",
    "R7": "нет окна без стоп-факторов или без пропусков данных — выбрать нельзя",
}


def _hm(iso_str: str) -> str:
    return iso_str[11:16]


def mech_summary(name: str, st: dict) -> str:
    m = st["minutes"]
    parts = []
    if m["critical"]:
        parts.append(f"критических {m['critical']:.0f} мин")
    if m["undesirable"]:
        parts.append(f"нежелательных {m['undesirable']:.0f} мин")
    if m["no_data"]:
        parts.append(f"без данных {m['no_data']:.0f} мин")
    if not parts:
        parts.append("без неблагоприятных условий")
    reasons = [f"{REASONS.get(r, r)} — {v:.0f} мин" for r, v in list(st["reasons"].items())[:3]]
    text = f"{MECH_TITLES.get(name, name)}: {', '.join(parts)}"
    if reasons:
        text += f" ({'; '.join(reasons)})"
    if "exposure_pfu_min" in st:
        text += f"; оценка потока выше порога обрезания за окно {st['exposure_pfu_min']:.0f} pfu·мин"
    return text + f"; уверенность {CONF_TEXT.get(st['confidence'], st['confidence'])}"


def window_summary(w: dict) -> str:
    head = f"{w['id']} {_hm(w['start'])}–{_hm(w['end'])} UTC: {STATUS_TEXT.get(w['status'], w['status'])}"
    body = ". ".join(mech_summary(n, s) for n, s in w["mechanisms"].items())
    ov = w["overrun"]
    tail = (f"Запас на задержку: критических факторов нет в течение {ov['margin_min']} мин после окончания"
            if ov["robust"] else f"Запас на задержку: первый критический фактор через "
                                 f"{ov['first_critical_after_end_min']:.0f} мин после окончания")
    return f"{head}. {body}. {tail}."


def conjunction_text(e: dict) -> str:
    """Сближение простыми словами: когда, с чем, как близко и что значит такая скорость."""
    return (f"сближение с {e['object_name']} ({e.get('object_type') or 'объект'}) в {_hm(e['tca'])} UTC на "
            f"{e['min_range_km']:.1f} км — проход через зону контроля; {e['pass_type']} пролёт, относительная скорость "
            f"{e['rel_speed_km_s']:.1f} км/с: при такой скорости 1 г вещества несёт ~{e['energy_per_gram_kj']:.0f} кДж "
            f"(≈{e['tnt_equiv_per_gram_g']:.0f} г тротила), поэтому размер объекта и вероятность попадания не взвешиваются")


def _conj_lines(w: dict, events: list[dict], label: str) -> list[str]:
    return [f"{label} {w['id']}: {conjunction_text(e)}." for e in events
            if e.get("in_control_box") and w["start"] <= e["tca"] < w["end"]][:3]


def build_explanation(ev: dict, notes: list[str], conjunctions: list[dict] | None = None) -> dict:
    windows = {w["id"]: w for w in ev["windows"]}
    conjunctions = conjunctions or []
    rec = ev["recommendation"]
    lines = []
    if rec["status"] in ("preferred", "equivalent"):
        best = windows[rec["window"]]
        lines.append(f"Рекомендуемое окно — {best['id']} ({_hm(best['start'])}–{_hm(best['end'])} UTC), "
                     f"правило {rec['rule']}: {RULE_TEXT.get(rec['rule'], '')}.")
        lines.append(window_summary(best))
        if rec.get("equivalent_windows"):
            n_eq = rec.get("n_equivalent", len(rec["equivalent_windows"]))
            more = f" и ещё {n_eq - len(rec['equivalent_windows'])}" if n_eq > len(rec["equivalent_windows"]) else ""
            lines.append(f"Равнозначных окон: {n_eq} (разница в пределах допуска): "
                         + ", ".join(rec["equivalent_windows"]) + more + ".")
        for t in rec.get("tradeoffs", []):
            lines.append(f"Компромисс: {t['window']} лучше по {', '.join(MECH_TITLES.get(x, x) for x in t['better_in'])}, "
                         f"но хуже по {', '.join(MECH_TITLES.get(x, x) for x in t['worse_in']) or '—'}; "
                         f"решено по правилу R4 ({t['resolved_by']}).")
        if rec["confidence"] == "low":
            lines.append("Уверенность низкая: часть окна оценена только по суточной вероятности или допущениям, "
                         "а дальше 12 ч с новыми орбитальными элементами могут появиться сближения — рекомендуется "
                         "пересчитать ближе к началу работ.")
    elif rec["status"] == "insufficient_basis":
        lines.append("Недостаточно оснований для рекомендации: во всех окнах без критических факторов "
                     "есть интервалы без данных. Отсутствие данных не означает благоприятную обстановку.")
    else:
        lines.append("Нет окна без критических факторов в заданном периоде поиска.")
    if ev.get("planned"):
        p = windows[ev["planned"]]
        if rec.get("window") != p["id"]:
            lines.append("Плановое окно: " + window_summary(p))
            lines.extend(_conj_lines(p, conjunctions, "Плановое окно"))
    lines.extend(notes)
    lines.append("Оценки описывают внешние условия и их пересечение с окном; это не доза облучения и не "
                 "вероятность поражения. Решение о выходе принимают уполномоченные специалисты.")
    return {"text": lines, "windows": {w["id"]: window_summary(w) for w in ev["windows"]}}
