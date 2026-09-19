"""Серверная отрисовка: полоса классов (SVG) для отчётов, CSV-таблицы, строка статуса."""
from __future__ import annotations

import csv
import io
from datetime import datetime

from common.timeutil import msk

CLASS_COLORS = {"critical": "#d64545", "undesirable": "#e0a030", "acceptable": "#3a9d5d", "no_data": "#9aa0a8"}
STATUS_TEXT = {"preferred": "предпочтительно", "equivalent": "равнозначно лучшему", "worse": "хуже",
               "not_recommended": "не рекомендуется", "requires_review": "требует проверки"}
REC_TEXT = {"preferred": "Предпочтительное окно", "equivalent": "Лучшие окна равнозначны",
            "insufficient_basis": "Недостаточно оснований для рекомендации",
            "no_window": "Нет окна без критических факторов"}
MODE_TEXT = {"now": "Текущая обстановка", "replay": "Прогноз из прошлого", "review": "Разбор (весь архив)"}
KIND_TEXT = {"observation": "наблюдение", "forecast_external": "внешний прогноз", "forecast_team": "расчёт команды",
             "probability": "вероятность", "forecast_baseline": "базовая модель", "none": "—"}
MECH_TEXT = {"radiation": "Радиация", "mmod": "Мусор и метеороиды"}


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", ""))


def class_strip_svg(res: dict, width: int = 980) -> str:
    """Полосы классов по механизмам + отметки T, лучшего и планового окна."""
    times = res["series"]["times"]
    t0, t1 = _t(times[0]), _t(times[-1])
    span = (t1 - t0).total_seconds() or 1
    x = lambda s: 110 + (width - 120) * ((_t(s) - t0).total_seconds() / span)
    rows = list(res["timeline"].keys())
    h = 26 * len(rows) + 40
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{h}" font-family="sans-serif" font-size="11">']
    for k, name in enumerate(rows):
        y = 10 + 26 * k
        parts.append(f'<text x="4" y="{y + 14}">{MECH_TEXT.get(name, name)}</text>')
        for iv in res["timeline"][name]:
            x0, x1 = x(iv["from"]), x(iv["to"])
            parts.append(f'<rect x="{x0:.1f}" y="{y}" width="{max(x1 - x0, 0.5):.1f}" height="20" '
                         f'fill="{CLASS_COLORS.get(iv["class"], "#ccc")}"><title>{msk(iv["from"], "%d.%m %H:%M")}–{msk(iv["to"])} МСК '
                         f'{iv["class"]}: {iv["reason_text"]} ({KIND_TEXT.get(iv["kind"], iv["kind"])})</title></rect>')
    base = 10 + 26 * len(rows)
    wins = {w["id"]: w for w in res["windows"]}
    for wid, color, label in ((res["recommendation"].get("window"), "#1b6fd6", "рекомендация"),
                              (res.get("planned"), "#555", "план")):
        if wid and wid in wins:
            w = wins[wid]
            parts.append(f'<rect x="{x(w["start"]):.1f}" y="6" width="{x(w["end"]) - x(w["start"]):.1f}" '
                         f'height="{base - 2}" fill="none" stroke="{color}" stroke-width="2"/>')
            parts.append(f'<text x="{x(w["start"]) + 2:.1f}" y="{base + 12}" fill="{color}">{label} {wid}</text>')
    xt = x(res["as_of"])
    if 110 <= xt <= width:
        parts.append(f'<line x1="{xt:.1f}" x2="{xt:.1f}" y1="2" y2="{base + 2}" stroke="#000" stroke-dasharray="4 3"/>')
        parts.append(f'<text x="{xt + 3:.1f}" y="{base + 26}">T = {msk(res["as_of"], "%d.%m %H:%M")}</text>')
    parts.append(f'<text x="110" y="{h - 2}">{msk(times[0], "%d.%m %H:%M")}</text>')
    parts.append(f'<text x="{width - 90}" y="{h - 2}">{msk(times[-1], "%d.%m %H:%M")} МСК</text>')
    parts.append("</svg>")
    return "".join(parts)


def csv_text(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def series_csv(res: dict) -> str:
    s = res["series"]
    keys = [k for k in s if k != "times"]
    return csv_text(["time_utc"] + keys, [[t] + [s[k][i] for k in keys] for i, t in enumerate(s["times"])])


def windows_csv(res: dict) -> str:
    rows = []
    for w in res["windows"]:
        r = [w["id"], w["start"], w["end"], w["status"], w["rule"], w["confidence"],
             w["overrun"]["first_critical_after_end_min"]]
        for m in res["timeline"]:
            st = w["mechanisms"][m]
            r += [st["minutes"]["critical"], st["minutes"]["undesirable"], st["minutes"]["no_data"],
                  st.get("exposure_pfu_min")]
        rows.append(r)
    header = ["id", "start", "end", "status", "rule", "confidence", "first_critical_after_end_min"]
    for m in res["timeline"]:
        header += [f"{m}_critical_min", f"{m}_undesirable_min", f"{m}_no_data_min", f"{m}_exposure_pfu_min"]
    return csv_text(header, rows)


def intervals_csv(res: dict) -> str:
    rows = [[m, iv["from"], iv["to"], iv["class"], iv["reason"], iv["kind"], iv["confidence"], ";".join(iv["evidence"])]
            for m, ivs in res["timeline"].items() for iv in ivs]
    return csv_text(["mechanism", "from", "to", "class", "reason", "kind", "confidence", "evidence"], rows)


def status_line(res: dict) -> str:
    rec = res["recommendation"]
    wins = {w["id"]: w for w in res["windows"]}
    w = wins.get(rec.get("window")) or wins.get(res.get("planned"))
    if w is None:
        return f"ВКД · {REC_TEXT.get(rec['status'], rec['status'])} · T {msk(res['as_of'], '%d.%m %H:%M')} МСК"
    marks = {"critical": "✖ крит", "undesirable": "▲ нежел", "no_data": "? нет данных", "acceptable": "● прием"}
    parts = [f"{'РАД' if m == 'radiation' else 'MMOD'} {marks[w['mechanisms'][m]['worst']]}" for m in w["mechanisms"]]
    ages = [s["age_s"] for s in res["sources"] if s.get("age_s") is not None and s["source"] == "goes_protons"]
    age = f" · данные {ages[0] // 60} мин" if ages and res["mode"] == "now" else ""
    nxt = f" · пересчёт {msk(res['recheck_after'][0]['time'])}" if res.get("recheck_after") else ""
    return f"ВКД {msk(w['start'])}–{msk(w['end'])} МСК · " + " · ".join(parts) + age + nxt
