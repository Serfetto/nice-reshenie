"""Выполнение расчёта: срез -> орбита -> механизмы -> окна -> объяснения -> результат (JSON)."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Callable

import numpy as np
from sqlalchemy.engine import Engine

from common import config
from common.db import get_engine
from common.timeutil import floor_to, from_np, iso, time_grid, utcnow
from services.assessment.core import orbit
from services.assessment.core.explain import build_explanation
from services.assessment.core.mmod import assess_mmod
from services.assessment.core.radiation import assess_radiation
from services.assessment.core.timeline import MechanismTimeline
from services.assessment.core.windows import evaluate_windows
from services.assessment.schemas import RunRequest
from services.assessment.slice import DataSlice, Override

HISTORY_FROM, HISTORY_TO = datetime(2024, 5, 1), datetime(2024, 7, 1)
RELEVANT_SOURCES = ["goes_protons", "swpc_alerts", "kp_observed", "kp_forecast", "swpc_3day", "swpc_rsga",
                    "iss_spacetrack", "iss_celestrak", "catalog_spacetrack"]


class RunError(Exception):
    pass


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return [jsonable(v) for v in o.tolist()]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else round(f, 6)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, datetime):
        return iso(o)
    return o


def _ceil_to(dt: datetime, minutes: int) -> datetime:
    f = floor_to(dt, minutes)
    return f if f == dt else f + timedelta(minutes=minutes)


def _recheck_after(as_of: datetime, until: datetime, best_start: datetime | None, horizon_h: float) -> list[dict]:
    out = []
    for src in ("swpc_3day", "swpc_rsga"):
        for hhmm in config.sources().get(src, {}).get("issue_times_utc", []) or []:
            hh, mm = map(int, hhmm.split(":"))
            t = as_of.replace(hour=hh, minute=mm, second=0, microsecond=0)
            while t <= as_of:
                t += timedelta(days=1)
            if t < until:
                out.append({"time": iso(t), "reason": f"выпуск {config.sources()[src]['title']}"})
    if best_start is not None:
        t = best_start - timedelta(hours=horizon_h)
        if t > as_of:
            out.append({"time": iso(t), "reason": f"начало окна войдёт в горизонт собственного прогноза ({horizon_h:.0f} ч)"})
    return sorted(out, key=lambda x: x["time"])


def execute(req: RunRequest, progress: Callable[[str, int], None] = lambda s, p: None,
            engine: Engine | None = None) -> dict:
    engine = engine or get_engine()
    cfg = config.thresholds()
    wc = cfg["window"]
    notes: list[str] = []

    as_of = utcnow() if req.mode == "now" else req.as_of
    step = req.step_min or wc["step_min"]
    earliest = req.earliest_start or (_ceil_to(as_of, step) if req.mode != "review" else as_of)
    latest = req.latest_start or earliest + timedelta(hours=wc["guaranteed_search_h"])
    if latest < earliest:
        raise RunError("Самое позднее начало раньше самого раннего")
    search_h = (latest - earliest).total_seconds() / 3600
    if search_h > wc["max_search_h"]:
        raise RunError(f"Период поиска {search_h:.0f} ч больше допустимых {wc['max_search_h']} ч")
    if search_h > wc["guaranteed_search_h"]:
        notes.append(f"Период поиска {search_h:.0f} ч больше {wc['guaranteed_search_h']} ч: уверенность по космической "
                     "погоде за пределами суток понижена.")
    if req.mode in ("replay", "review") and not (HISTORY_FROM <= as_of < HISTORY_TO):
        notes.append("Дата вне проверочного периода 01.05–30.06.2024: работа с архивом не гарантирована.")
    if req.mode == "review":
        notes.append("Режим разбора: использован весь доступный сегодня архив, включая поздние уточнения "
                     "(реконструкция, а не прогноз из прошлого).")
    margin = req.eva.overrun_margin_min
    overrides = {k: Override(v.state, v.at) for k, v in req.source_overrides.items()}

    progress("slice", 5)
    sl = DataSlice(engine, req.mode, as_of, overrides)
    grid_step = int(cfg["grid_step_s"])
    g0 = floor_to(min(earliest, as_of) - timedelta(hours=cfg["context_before_h"]), 1)
    g1 = latest + timedelta(minutes=req.duration_min + margin)
    grid = time_grid(g0, g1, grid_step)

    progress("orbit", 15)
    el, el_source = sl.iss_elements(epoch_not_after=from_np(grid[len(grid) // 2]))
    if el is None:
        raise RunError("Нет орбитальных элементов МКС, опубликованных до момента отсечки.")
    el_age_h = (as_of - el.epoch).total_seconds() / 3600
    if el_age_h > 72:
        notes.append(f"Орбитальные элементы МКС старые ({el_age_h:.0f} ч от эпохи) — точность положения снижена.")
    sat = orbit.satrec(el.line1, el.line2)
    track = orbit.build_track(sat, grid, from_np(grid[len(grid) // 2]))
    if np.any(track.error != 0):
        raise RunError("SGP4 не смог распространить орбиту МКС на весь интервал.")

    mechs: dict[str, MechanismTimeline] = {}
    if "radiation" in req.mechanisms:
        progress("radiation", 35)
        mechs["radiation"] = assess_radiation(sl, track, cfg, model=req.radiation_model)
    if "mmod" in req.mechanisms:
        progress("mmod", 60)
        mechs["mmod"] = assess_mmod(sl, track, sat, cfg)

    progress("windows", 85)
    ev = evaluate_windows(mechs, earliest, latest, req.duration_min, req.planned_start, margin,
                          {**cfg, "window": {**wc, "step_min": step}})
    for m in mechs.values():
        notes.extend(m.notes)
    explanation = build_explanation(ev, notes)

    rec = ev["recommendation"]
    best = next((w for w in ev["windows"] if w["id"] == rec.get("window")), None)
    best_start = datetime.fromisoformat(best["start"][:-1]) if best else None

    # доказательства: только те, на которые есть ссылки
    evidence: dict[str, dict] = {}
    timeline = {}
    for name, m in mechs.items():
        timeline[name] = m.intervals()
        used = {k for iv in timeline[name] for k in iv["evidence"]}
        evidence.update({k: v for k, v in m.evidence_items.items() if k in used or k.startswith("conj:")})
    evidence["orbit"] = {
        "layer": "measurement", "kind": "elements", "source": el_source, "norad_id": el.norad_id,
        "epoch": iso(el.epoch), "created": iso(el.creation_date), "creation_policy": el.creation_policy,
        "age_at_cutoff_h": round(el_age_h, 2), "line1": el.line1, "line2": el.line2,
        "method": "SGP4 (TEME) -> ITRF по GMST -> WGS84; IGRF-14", "raw_ref": {"raw_id": el.raw_id}}

    k = max(1, int(cfg["output_step_s"]) // grid_step)
    sel = slice(0, len(grid), k)
    series = {"times": [iso(from_np(t)) for t in grid[sel]],
              "lat": track.lat[sel], "lon": track.lon[sel], "alt_km": track.alt[sel], "mlat": track.mlat[sel],
              "b_nt": track.b_nt[sel], "sunlit": track.sunlit[sel].astype(float)}
    for m in mechs.values():
        for key, arr in m.series.items():
            series[f"{m.name}.{key}"] = np.asarray(arr)[sel]
        series[f"{m.name}.class"] = m.cls[sel]

    sources = [sl.freshness(s) for s in RELEVANT_SOURCES]
    manifest = sl.manifest()
    reconstruction = req.mode == "review" or any(v["reconstructed"] for v in manifest.values())
    progress("done", 100)
    return jsonable({
        "mode": req.mode, "as_of": as_of, "data_cutoff": sl.data_cutoff,
        "algorithm_version": config.ALGORITHM_VERSION, "radiation_model": req.radiation_model,
        "search": {"earliest_start": earliest, "latest_start": latest, "duration_min": req.duration_min,
                   "step_min": step, "overrun_margin_min": margin,
                   "return_to_airlock_min": req.eva.return_to_airlock_min},
        "orbit": evidence["orbit"],
        "sources": sources, "manifest": manifest, "reconstruction": reconstruction,
        "timeline": timeline, "series": series,
        "windows": ev["windows"], "recommendation": rec, "planned": ev["planned"],
        "priority": ev["priority"],
        "events": {name: m.events for name, m in mechs.items()},
        "evidence": evidence, "explanation": explanation,
        "recheck_after": _recheck_after(as_of, latest + timedelta(minutes=req.duration_min), best_start,
                                        cfg["radiation"]["forecast_horizon_h"]),
        "limitations": [
            "Поток протонов GOES измерен на геостационаре; J_iss — оценка потока выше порога обрезания в точке МКС, "
            "а не доза.",
            "Жёсткость обрезания — приближение Штёрмера для центрированного диполя с эмпирическим сдвигом по Kp.",
            "Сближения рассчитаны по TLE (точность единицы км); мелкие фрагменты не каталогизированы; вероятность "
            "попадания в космонавта не оценивается.",
            "Классы и пороги — черновые, обоснование в docs/method.md.",
        ],
        "notes": notes,
    })
