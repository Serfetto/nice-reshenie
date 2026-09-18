"""Механизм 2: сближения МКС с отслеживаемыми объектами (MMOD).

Собственный отбор: объекты каталога Space-Track, пересекающие полосу высот МКС, распространяются
SGP4 на той же сетке, что и МКС; кандидаты уточняются вокруг локальных минимумов расстояния.
Сближение с проходом через зону контроля МКС (радиальная × вдоль орбиты × поперёк) — стоп-фактор
по бинарному принципу. Вероятность попадания фрагмента в космонавта не оценивается.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np

from common.timeutil import from_np, iso, to_np
from services.assessment.core import orbit
from services.assessment.core.timeline import ACCEPTABLE, CRITICAL, NO_DATA, MechanismTimeline
from services.assessment.slice import DataSlice

SOURCE = "catalog_spacetrack"


def _ric(r_iss: np.ndarray, v_iss: np.ndarray, rel: np.ndarray) -> tuple[float, float, float]:
    rh = r_iss / np.linalg.norm(r_iss)
    ch = np.cross(r_iss, v_iss)
    ch /= np.linalg.norm(ch)
    ih = np.cross(ch, rh)
    return float(rel @ rh), float(rel @ ih), float(rel @ ch)


def _refine(iss_sat, obj_sat, t_center: np.datetime64, half_s: int) -> tuple[np.datetime64, float, tuple, float]:
    """Минимум расстояния в окрестности t_center с шагом 0.5 с."""
    fine = t_center + np.arange(-2 * half_s, 2 * half_s + 1) * np.timedelta64(500, "ms")
    fine = fine.astype("datetime64[ms]")
    sec = fine.astype("datetime64[ms]").astype(np.int64) / 1000.0
    jd_full = 2440587.5 + sec / 86400.0
    jd = np.floor(jd_full - 0.5) + 0.5
    fr = jd_full - jd
    _, ri, vi = iss_sat.sgp4_array(jd, fr)
    _, ro, vo = obj_sat.sgp4_array(jd, fr)
    d = np.linalg.norm(ro - ri, axis=1)
    k = int(np.nanargmin(d))
    rel_v = float(np.linalg.norm(vo[k] - vi[k]))
    return fine[k].astype("datetime64[s]"), float(d[k]), _ric(ri[k], vi[k], ro[k] - ri[k]), rel_v


def assess_mmod(sl: DataSlice, track: orbit.Track, iss_sat, cfg: dict) -> MechanismTimeline:
    c = cfg["mmod"]
    grid = track.times
    n = len(grid)
    g0 = from_np(grid[0])
    cls = np.full(n, NO_DATA, dtype=np.int8)
    reason = np.full(n, "no_catalog", dtype=object)
    kind = np.full(n, "none", dtype=object)
    conf = np.full(n, "none", dtype=object)
    ev: list[tuple] = [()] * n
    evidence: dict[str, dict] = {}
    notes: list[str] = []

    fr = sl.freshness(SOURCE)
    # эпоха не позже середины интервала: в разборе прошлого не берём элементы «из будущего»
    mid = from_np(grid[n // 2])
    catalog = sl.catalog_elements(g0, c["max_element_age_days"], epoch_not_after=mid)
    if fr["state"] in ("disabled", "no_data") or not catalog:
        if fr["state"] == "disabled":
            reason[:] = "source_disabled"
        notes.append("Каталог объектов недоступен — сближения не оценены.")
        return MechanismTimeline("mmod", grid, cls, reason, kind, conf, ev, evidence, notes=notes)
    if fr["state"] in ("stale", "frozen_stale"):
        reason[:] = "catalog_stale"
        notes.append(f"Каталог объектов устарел (последняя публикация {fr['latest_data']}) — сближения не оценены.")
        return MechanismTimeline("mmod", grid, cls, reason, kind, conf, ev, evidence, notes=notes)

    step_s = int((grid[1] - grid[0]) / np.timedelta64(1, "s")) if n > 1 else 30
    objs = list(catalog.values())
    sats = [orbit.satrec(o.line1, o.line2) for o in objs]
    r_iss = track.r_teme
    coarse_km = c["report_range_km"] + 16.0 * step_s / 2  # относительная скорость до ~16 км/с
    events, docked = [], []
    for k0 in range(0, len(objs), 300):
        chunk = sats[k0:k0 + 300]
        r, _, e = orbit.propagate_many(chunk, grid)
        d = np.linalg.norm(r - r_iss[None, :, :], axis=-1)
        d[e != 0] = np.inf
        for j in range(len(chunk)):
            dj = d[j]
            obj = objs[k0 + j]
            if np.isfinite(dj).sum() > n // 2 and np.nanmedian(dj[np.isfinite(dj)]) < c["docked_median_km"]:
                docked.append(obj.norad_id)
                continue
            cand = np.nonzero(dj < coarse_km)[0]
            if len(cand) == 0:
                continue
            for i in cand:
                left = dj[i - 1] if i > 0 else np.inf
                right = dj[i + 1] if i < n - 1 else np.inf
                if dj[i] <= left and dj[i] <= right:
                    tca, rng, (dr, di, dc), vrel = _refine(iss_sat, chunk[j], grid[i], step_s)
                    if rng > c["report_range_km"]:
                        continue
                    in_box = (abs(dr) <= c["box_radial_km"] and abs(di) <= c["box_intrack_km"]
                              and abs(dc) <= c["box_crosstrack_km"])
                    age_d = (from_np(tca) - obj.epoch).total_seconds() / 86400
                    events.append({
                        "norad_id": obj.norad_id, "object_name": obj.object_name, "object_type": obj.object_type,
                        "tca": iso(from_np(tca)), "_tca": tca, "min_range_km": round(rng, 3),
                        "radial_km": round(dr, 3), "intrack_km": round(di, 3), "crosstrack_km": round(dc, 3),
                        "rel_speed_km_s": round(vrel, 3), "in_control_box": in_box,
                        "element_epoch": iso(obj.epoch), "element_created": iso(obj.creation_date),
                        "element_age_days": round(age_d, 2), "raw_id": obj.raw_id})

    # всё, что не критично, — приемлемо: каталог есть и отбор выполнен
    cls[:] = ACCEPTABLE
    reason[:] = "nominal"
    kind[:] = "forecast_team"
    conf[:] = "medium"
    ev = [("catalog",)] * n
    pad = np.timedelta64(int(c["tca_pad_min"] * 60), "s")
    for evn in sorted(events, key=lambda x: x["tca"]):
        t = evn.pop("_tca")
        key = f"conj:{evn['norad_id']}:{evn['tca']}"
        evidence[key] = {"layer": "condition", "kind": "forecast_team", "source": SOURCE, **evn,
                         "raw_ref": {"raw_id": evn["raw_id"], "locator": f"NORAD_CAT_ID:{evn['norad_id']}"}}
        if not evn["in_control_box"]:
            continue
        m = (grid >= t - pad) & (grid <= t + pad)
        cls[m] = CRITICAL
        reason[m] = "conjunction"
        conf[m] = "medium" if evn["element_age_days"] <= 2 else "low"
        for i in np.nonzero(m)[0]:
            ev[i] = (key, "catalog")

    evidence["catalog"] = {
        "layer": "measurement", "kind": "catalog", "source": SOURCE,
        "objects_screened": len(objs), "excluded_docked": docked,
        "latest_published": fr["latest_data"], "cutoff": iso(sl.cutoff(SOURCE)),
        "control_box_km": {"radial": c["box_radial_km"], "intrack": c["box_intrack_km"],
                           "crosstrack": c["box_crosstrack_km"]},
        "method": "SGP4 по элементам, опубликованным до отсечки; уточнение минимума с шагом 0.5 с",
        "limitations": "точность TLE — единицы км и растёт с возрастом элементов; мелкие фрагменты "
                       "не каталогизированы; вероятность попадания не оценивается"}
    if docked:
        notes.append(f"Из отбора сближений исключены {len(docked)} объектов, пристыкованных к МКС "
                     "(движутся вместе с ней; список — в доказательстве «catalog»).")
    return MechanismTimeline("mmod", grid, cls, reason, kind, conf, ev, evidence,
                             events=sorted(events, key=lambda x: x["tca"]), notes=notes)
