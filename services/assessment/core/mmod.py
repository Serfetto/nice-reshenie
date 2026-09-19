"""Механизм 2: микрометеороиды и орбитальный мусор (MMOD).

Отслеживаемые объекты — собственный отбор: объекты каталога Space-Track, пересекающие полосу высот МКС,
распространяются SGP4 на той же сетке, что и МКС; кандидаты уточняются вокруг локальных минимумов расстояния.
Сближение с проходом через зону контроля МКС (радиальная × вдоль орбиты × поперёк) — стоп-фактор
по бинарному принципу: при относительной скорости порядка 10 км/с даже малый фрагмент несёт энергию
взрыва, поэтому размер и вероятность попадания не взвешиваются (и не оцениваются).
Неотслеживаемые частицы — статистически, по прогнозу метеорных потоков (core/meteors.py).
"""
from __future__ import annotations


import numpy as np

from common.timeutil import from_np, iso, to_np
from services.assessment.core import orbit
from services.assessment.core.meteors import meteor_layer
from services.assessment.core.timeline import ACCEPTABLE, CRITICAL, NO_DATA, UNDESIRABLE, MechanismTimeline
from services.assessment.slice import DataSlice

SOURCE = "catalog_spacetrack"


def _ric(r_iss: np.ndarray, v_iss: np.ndarray, rel: np.ndarray) -> tuple[float, float, float]:
    rh = r_iss / np.linalg.norm(r_iss)
    ch = np.cross(r_iss, v_iss)
    ch /= np.linalg.norm(ch)
    ih = np.cross(ch, rh)
    return float(rel @ rh), float(rel @ ih), float(rel @ ch)


def _refine(iss_sat, obj_sat, t_center: np.datetime64, half_s: int) -> tuple[np.datetime64, float, tuple, float, float]:
    """Минимум расстояния в окрестности t_center с шагом 0.5 с -> (TCA, дальность, RIC, отн. скорость, угол скоростей)."""
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
    cosang = float(vi[k] @ vo[k] / (np.linalg.norm(vi[k]) * np.linalg.norm(vo[k])))
    angle = float(np.degrees(np.arccos(np.clip(cosang, -1, 1))))
    return fine[k].astype("datetime64[s]"), float(d[k]), _ric(ri[k], vi[k], ro[k] - ri[k]), rel_v, angle


def impact_physics(rel_speed_km_s: float, angle_deg: float) -> dict:
    """Что значит скорость сближения: тип пролёта и кинетическая энергия 1 г вещества при такой скорости."""
    e_kj = 0.5 * rel_speed_km_s ** 2  # ½·1 г·v², v в км/с -> кДж
    kind = "встречный" if angle_deg >= 120 else "поперечный" if angle_deg >= 60 else "попутный"
    return {"pass_type": kind, "crossing_angle_deg": round(angle_deg, 1),
            "energy_per_gram_kj": round(e_kj, 1), "tnt_equiv_per_gram_g": round(e_kj / 4.184, 1)}


def co_orbiting(d: np.ndarray, vrel: np.ndarray, c: dict) -> bool:
    """Объект летит вместе с МКС: пристыкованный корабль или модуль, только что отстыкованный корабль.

    У таких объектов свой TLE, который из-за разной эпохи расходится с TLE МКС на километры, поэтому
    одного критерия по расстоянию мало: признак — малая относительная скорость на всём интервале.
    Сближение в смысле механизма — пересечение орбит с относительной скоростью порядка км/с."""
    ok = np.isfinite(d) & np.isfinite(vrel)
    if ok.sum() <= len(d) // 2:
        return False
    return bool(np.median(d[ok]) < c["docked_median_km"] or np.median(vrel[ok]) < c["coorbit_rel_speed_km_s"])


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
    r_iss, v_iss = track.r_teme, track.v_teme
    coarse_km = c["report_range_km"] + 16.0 * step_s / 2  # относительная скорость до ~16 км/с
    events, coorbit = [], []
    for k0 in range(0, len(objs), 300):
        chunk = sats[k0:k0 + 300]
        r, v, e = orbit.propagate_many(chunk, grid)
        d = np.linalg.norm(r - r_iss[None, :, :], axis=-1)
        spd = np.linalg.norm(v - v_iss[None, :, :], axis=-1)
        d[e != 0] = np.inf
        spd[e != 0] = np.nan
        for j in range(len(chunk)):
            dj = d[j]
            obj = objs[k0 + j]
            if co_orbiting(dj, spd[j], c):
                if np.min(dj) <= c["report_range_km"]:
                    coorbit.append({"norad_id": obj.norad_id, "object_name": obj.object_name,
                                    "min_range_km": round(float(np.min(dj)), 1),
                                    "median_rel_speed_km_s": round(float(np.nanmedian(spd[j])), 4)})
                continue
            cand = np.nonzero(dj < coarse_km)[0]
            if len(cand) == 0:
                continue
            for i in cand:
                left = dj[i - 1] if i > 0 else np.inf
                right = dj[i + 1] if i < n - 1 else np.inf
                if dj[i] <= left and dj[i] <= right:
                    tca, rng, (dr, di, dc), vrel, angle = _refine(iss_sat, chunk[j], grid[i], step_s)
                    if rng > c["report_range_km"]:
                        continue
                    in_box = (abs(dr) <= c["box_radial_km"] and abs(di) <= c["box_intrack_km"]
                              and abs(dc) <= c["box_crosstrack_km"])
                    age_d = (from_np(tca) - obj.epoch).total_seconds() / 86400
                    events.append({
                        "norad_id": obj.norad_id, "object_name": obj.object_name, "object_type": obj.object_type,
                        "tca": iso(from_np(tca)), "_tca": tca, "min_range_km": round(rng, 3),
                        "radial_km": round(dr, 3), "intrack_km": round(di, 3), "crosstrack_km": round(dc, 3),
                        "rel_speed_km_s": round(vrel, 3), **impact_physics(vrel, angle), "in_control_box": in_box,
                        "element_epoch": iso(obj.epoch), "element_created": iso(obj.creation_date),
                        "element_age_days": round(age_d, 2), "raw_id": obj.raw_id})

    # всё, что не критично, — приемлемо: каталог есть и отбор выполнен
    cls[:] = ACCEPTABLE
    reason[:] = "nominal"
    kind[:] = "forecast_team"
    conf[:] = "medium"
    ev = [("catalog",)] * n
    pad = np.timedelta64(int(c["tca_pad_min"] * 60), "s")
    conf_reason = np.full(n, "каталог, опубликованный до отсечки, и SGP4; фрагменты меньше ~10 см не каталогизированы",
                          dtype=object)
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
        # по проверке на 01.05–30.06.2024 (docs/experiments.md, §5) сближение, предсказанное за ≤ 12 ч,
        # подтверждается более свежими элементами в ~90–96 % случаев, за 24–32 ч — в ~77 %
        lead_h = (from_np(t) - sl.as_of).total_seconds() / 3600
        near = lead_h <= c["conj_confident_lead_h"] and evn["element_age_days"] <= 2
        conf[m] = "medium" if near else "low"
        conf_reason[m] = ("сближение через ≤ 12 ч по свежим элементам — по проверке подтверждается в ~90–96 % случаев"
                          if near else "сближение дальше 12 ч или по элементам старше 2 сут — с более свежими "
                                       "элементами подтверждается в ~77–85 % случаев, пересчитать ближе к делу")
        for i in np.nonzero(m)[0]:
            ev[i] = (key, "catalog")

    # дальше 12 ч новые сближения появляются с более свежими элементами (каждое 4–5-е за 12–32 ч, experiments.md, §5)
    far = (reason == "nominal") & ((grid - to_np(sl.as_of)) > np.timedelta64(int(c["conj_confident_lead_h"] * 3600), "s"))
    conf[far] = "low"
    conf_reason[far] = ("дальше 12 ч от момента расчёта: с более свежими элементами появляется примерно каждое "
                        "четвёртое сближение — пересчитать ближе к делу")

    evidence["catalog"] = {
        "layer": "measurement", "kind": "catalog", "source": SOURCE,
        "objects_screened": len(objs), "excluded_coorbiting": coorbit,
        "latest_published": fr["latest_data"], "cutoff": iso(sl.cutoff(SOURCE)),
        "control_box_km": {"radial": c["box_radial_km"], "intrack": c["box_intrack_km"],
                           "crosstrack": c["box_crosstrack_km"]},
        "method": "SGP4 по элементам, опубликованным до отсечки; уточнение минимума с шагом 0.5 с",
        "limitations": "точность TLE — единицы км и растёт с возрастом элементов; мелкие фрагменты "
                       "не каталогизированы; вероятность попадания не оценивается"}
    if coorbit:
        notes.append(f"Из отбора сближений исключены {len(coorbit)} объектов, летящих вместе с МКС "
                     f"(пристыкованные корабли и модули, относительная скорость < {c['coorbit_rel_speed_km_s']} км/с; "
                     "список — в доказательстве «catalog»).")

    # статистическая часть: метеорные потоки там, где сближений нет
    met = meteor_layer(sl, track, cfg)
    evidence.update(met["evidence"])
    notes.extend(met["notes"])
    m = met["undesirable"] & (cls == ACCEPTABLE)
    cls[m] = UNDESIRABLE
    reason[m] = "meteor_shower"
    kind[m] = "forecast_external"
    conf[m] = "low"
    for i in np.nonzero(m)[0]:
        ev[i] = ("meteor_forecast", "meteor_geometry")
    if met["available"]:  # спокойные интервалы тоже опираются на прогноз потоков — показываем его в доказательствах
        ev = [x if x != ("catalog",) else ("catalog", "meteor_forecast", "meteor_geometry") for x in ev]
    series = {"meteor_factor": met["factor"], "meteor_visible": met["visible"].astype(float)}
    conf_reason[reason == "meteor_shower"] = "статистический годовой прогноз NASA: поток частиц, а не отдельные частицы"
    return MechanismTimeline("mmod", grid, cls, reason, kind, conf, ev, evidence, series=series,
                             events=sorted(events, key=lambda x: x["tca"]), notes=notes, conf_reason=conf_reason)
