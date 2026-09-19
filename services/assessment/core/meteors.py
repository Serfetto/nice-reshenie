"""Метеорные потоки — статистическая часть механизма MMOD (частицы, которые не видны поштучно).

Опасность мелких частиц — в скорости: метеороиды потоков приходят со скоростью 35–66 км/с, и частица
в доли миллиметра способна пробить тонкую оболочку. Отдельные частицы не прогнозируются, поэтому
используется прогноз NASA MEO: почасовой коэффициент потока метеоров потоков к спорадическому фону
для заданной предельной энергии (скафандр — тонкая оболочка: берём наименьшую энергию прогноза).

Привязка к траектории: поток виден, только если радиант не закрыт Землёй (с атмосферой ~100 км) в точке
МКС. Радиант — у ближайшего по времени крупного потока из таблицы 2 прогноза, со сдвигом (аберрацией)
на скорость станции. Класс не выше «нежелательно»: это рост вероятности, а не событие.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np

from common.timeutil import from_np, iso, to_np
from services.assessment.core import orbit
from services.assessment.slice import DataSlice

SOURCE = "meteor_forecast"
ATMOSPHERE_KM = 100.0


def radiant_unit(ra_deg: float, dec_deg: float) -> np.ndarray:
    ra, dec = np.deg2rad(ra_deg), np.deg2rad(dec_deg)
    return np.array([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])


def radiant_visible(r: np.ndarray, v: np.ndarray, s: np.ndarray, speed_km_s: float) -> np.ndarray:
    """Не закрыт ли радиант Землёй для станции в точке r (км, инерциальная система) со скоростью v (км/с).

    Видимый радиант — направление, откуда частицы приходят относительно станции: s·V + v."""
    app = s[None, :] * speed_km_s + v
    app /= np.linalg.norm(app, axis=-1, keepdims=True)
    rn = np.linalg.norm(r, axis=-1)
    cos_rho = np.cos(np.arcsin(np.clip((orbit.EARTH_R_KM + ATMOSPHERE_KM) / rn, -1, 1)))
    return np.sum(app * r, axis=-1) / rn > -cos_rho


def meteor_layer(sl: DataSlice, track: orbit.Track, cfg: dict) -> dict:
    c = cfg["meteor"]
    grid = track.times
    n = len(grid)
    g0, g1 = from_np(grid[0]), from_np(grid[-1])
    q = f"meteor_factor_{c['energy_label']}"
    rows = sl.latest_forecasts([SOURCE], q, g0 - timedelta(hours=1), g1 + timedelta(hours=1))
    out = {"available": False, "factor": np.full(n, np.nan), "visible": np.zeros(n, dtype=bool),
           "undesirable": np.zeros(n, dtype=bool), "evidence": {}, "notes": []}
    if not rows:
        out["notes"].append(f"Прогноз метеорных потоков NASA на {g0.year} г., опубликованный до отсечки, не найден — "
                            "метеорный фактор не оценён (сближения с каталогом оцениваются).")
        return out
    t_rows = np.array([to_np(r.valid_from) for r in rows])
    v_rows = np.array([r.value for r in rows], dtype=float)
    idx = np.searchsorted(t_rows, grid, side="right") - 1
    ok = (idx >= 0) & (grid < t_rows[np.clip(idx, 0, None)] + np.timedelta64(3600, "s"))
    factor = np.where(ok, v_rows[np.clip(idx, 0, None)], np.nan)

    rad = sl.records(SOURCE, "meteor_radiant", g0 - timedelta(days=90), g1 + timedelta(days=90), "forecast")
    visible = np.ones(n, dtype=bool)
    used = []
    if rad:
        peaks = np.array([to_np(r.valid_from) for r in rad])
        near = np.argmin(np.abs((grid[:, None] - peaks[None, :]).astype("timedelta64[s]").astype(float)), axis=1)
        for k in np.unique(near):
            r = rad[k]
            m = near == k
            s = radiant_unit(r.quality["ra_deg"], r.quality["dec_deg"])
            visible[m] = radiant_visible(track.r_teme[m], track.v_teme[m], s, r.value)
            used.append({"shower": r.quality["name"], "peak": iso(r.valid_from), "ra_deg": r.quality["ra_deg"],
                         "dec_deg": r.quality["dec_deg"], "speed_km_s": r.value})
    else:
        out["notes"].append("Радианты потоков не найдены — экранирование Землёй не учтено (консервативно).")
    undesirable = np.isfinite(factor) & (factor >= c["undesirable_factor"]) & visible
    issued = sorted({r.issued_at for r in rows})
    out.update(available=True, factor=factor, visible=visible, undesirable=undesirable)
    out["evidence"]["meteor_forecast"] = {
        "layer": "forecast", "kind": "forecast_external", "source": SOURCE,
        "quantity": f"поток метеоров потоков к спорадическому фону, предельная энергия {c['energy_label']}",
        "unit": "отношение", "issued_at": iso(issued[-1]), "max_factor_in_interval": round(float(np.nanmax(factor)), 3),
        "threshold": c["undesirable_factor"],
        "raw_ref": {"raw_ids": sorted({r.raw_id for r in rows})[:5]}}
    out["evidence"]["meteor_geometry"] = {
        "layer": "condition", "kind": "forecast_team", "source": "team",
        "model": "радиант закрыт Землёй с атмосферой 100 км в точке МКС; видимый радиант с учётом скорости станции",
        "showers": used, "visible_share": round(float(visible.mean()), 3),
        "note": "радиант — ближайшего по времени крупного потока из таблицы 2 прогноза; поток — сумма всех потоков",
        "raw_ref": {"raw_ids": sorted({r.raw_id for r in rad})[:5]}}
    return out
