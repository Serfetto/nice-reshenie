"""Механизм 1: радиационная обстановка на траектории МКС (солнечные протоны + ЮАА).

Три слоя:
1. Измерение: интегральный поток протонов GOES (геостационар) по порогам энергии, Kp.
2. Условие на траектории: жёсткость обрезания Rc в точке МКС (с поправкой на Kp) задаёт
   минимальную энергию протонов, которые туда проникают; поток выше этой энергии J_iss
   оценивается по спектру GOES. Плюс флаг ЮАА.
3. Последствие для ВКД: класс по порогам config/thresholds.yaml.

После момента отсечки обстановка снаружи берётся по приоритету:
действующее предупреждение SWPC (внешний прогноз) + собственный прогноз на 6 ч (сохранение
или экспоненциальный спад) -> суточная вероятность SWPC -> «нет данных».
J_iss — показатель обстановки, а не доза.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from common.timeutil import from_np, iso, to_np
from services.assessment.core import orbit
from services.assessment.core.timeline import (ACCEPTABLE, CRITICAL, NO_DATA, UNDESIRABLE, MechanismTimeline)
from services.assessment.slice import DataSlice

SOURCE = "goes_protons"
PRIORITY = {"estimated": 0, "exact": 0, "fetched": 1, "reconstructed": 2}


def _obs_series(sl: DataSlice, quantity: str, t0: datetime, t1: datetime):
    rows = sl.records(SOURCE, quantity, t0, t1, "observation")
    best: dict[datetime, object] = {}
    for r in rows:
        cur = best.get(r.valid_from)
        if cur is None or PRIORITY.get(r.issued_at_policy, 3) < PRIORITY.get(cur.issued_at_policy, 3):
            best[r.valid_from] = r
    keys = sorted(best)
    t = np.array([to_np(k) for k in keys], dtype="datetime64[s]")
    v = np.array([best[k].value for k in keys], dtype=float)
    return t, v, [best[k] for k in keys]


def _map_samples(grid: np.ndarray, t: np.ndarray, v: np.ndarray, max_gap_s: int, until: np.datetime64):
    out = np.full(len(grid), np.nan)
    idx = np.full(len(grid), -1)
    if len(t) == 0:
        return out, idx
    i = np.searchsorted(t, grid, side="right") - 1
    ok = (i >= 0) & (grid <= until)
    ok[ok] &= (grid[ok] - t[i[ok]]) <= np.timedelta64(max_gap_s, "s")
    out[ok] = v[i[ok]]
    idx[ok] = i[ok]
    return out, idx


def _map_intervals(grid: np.ndarray, rows) -> tuple[np.ndarray, list]:
    """Значения интервальных записей (valid_from <= t < valid_to) на сетку."""
    out = np.full(len(grid), np.nan)
    src: list = [None] * len(grid)
    for r in rows:
        m = (grid >= to_np(r.valid_from)) & (grid < to_np(r.valid_to))
        out[m] = r.value
        for k in np.nonzero(m)[0]:
            src[k] = r
    return out, src


def integral_above(e_cut: np.ndarray, energies: list[float], j: np.ndarray) -> np.ndarray:
    """J(>E_cut) по интегральным потокам на порогах energies (лог-лог интерполяция)."""
    le = np.log(np.asarray(energies, dtype=float))
    out = np.full(len(e_cut), np.nan)
    for k in range(len(e_cut)):
        row = j[k]
        ok = np.isfinite(row) & (row > 0)
        if not ok[0] or np.isnan(e_cut[k]):
            continue
        vals = np.minimum.accumulate(np.where(ok, row, np.inf))  # поток не растёт с энергией
        vals = np.where(np.isfinite(vals), vals, np.nan)
        ok = np.isfinite(vals)
        x, y = le[ok], np.log(vals[ok])
        le_c = np.log(max(e_cut[k], energies[0]))
        if le_c <= x[-1] or len(x) < 2:
            out[k] = np.exp(np.interp(le_c, x, y))
        else:
            slope = min((y[-1] - y[-2]) / (x[-1] - x[-2]), 0.0)
            out[k] = np.exp(y[-1] + slope * (le_c - x[-1]))
    return out


def _decay_rate(t: np.ndarray, v: np.ndarray, last: np.datetime64, window_h: float) -> float | None:
    """Наклон ln(потока) в час по последним window_h часам."""
    m = (t > last - np.timedelta64(int(window_h * 3600), "s")) & (t <= last) & (v > 0)
    if m.sum() < 6:
        return None
    x = (t[m] - last).astype(float) / 3600.0
    return float(np.polyfit(x, np.log(v[m]), 1)[0])


def _warning_intervals(msgs, codes: tuple[str, ...]) -> list[dict]:
    """Интервалы действия предупреждений с учётом продлений и отмен."""
    chains: dict[int, dict] = {}
    by_serial = {m.serial: m for m in msgs if m.code in codes}
    for m in sorted([m for m in msgs if m.code in codes], key=lambda x: x.issued_at):
        root = m.serial
        seen = set()
        while by_serial.get(root) is not None and by_serial[root].extension_of and root not in seen:
            seen.add(root)
            root = by_serial[root].extension_of
        if (m.msg_type or "").startswith("CANCEL"):
            target = m.cancel_of or m.extension_of
            if target in chains:
                chains[target]["valid_to"] = min(chains[target]["valid_to"], m.issued_at)
            continue
        if m.valid_from is None or m.valid_to is None:
            continue
        ch = chains.setdefault(root, {"code": m.code, "valid_from": m.valid_from, "valid_to": m.valid_to,
                                      "messages": []})
        ch["valid_from"] = min(ch["valid_from"], m.valid_from)
        ch["valid_to"] = m.valid_to  # последнее продление задаёт конец
        ch["messages"].append(m)
    return list(chains.values())


def _msg_evidence(m) -> tuple[str, dict]:
    key = f"msg:{m.code}:{m.serial}"
    return key, {"layer": "measurement" if m.msg_type in ("ALERT", "SUMMARY") else "forecast",
                 "kind": "message", "source": "swpc_alerts", "code": m.code, "serial": m.serial,
                 "title": m.title, "issued_at": iso(m.issued_at), "valid_from": iso(m.valid_from),
                 "valid_to": iso(m.valid_to), "begin_time": iso(m.begin_time), "end_time": iso(m.end_time),
                 "scale": m.scale, "raw_ref": {"raw_id": m.raw_id, "locator": m.locator}}


def assess_radiation(sl: DataSlice, track: orbit.Track, cfg: dict, model: str = "team") -> MechanismTimeline:
    c = cfg["radiation"]
    grid = track.times
    n = len(grid)
    g0, g1 = from_np(grid[0]), from_np(grid[-1])
    t_known = sl.data_cutoff if sl.mode == "review" else sl.as_of
    ov = sl.overrides.get(SOURCE)
    if ov is not None and ov.state == "frozen" and ov.at:
        t_known = min(t_known, ov.at)
    T = to_np(t_known)
    energies = [float(e) for e in c["energies_mev"]]
    evidence: dict[str, dict] = {}
    notes: list[str] = []
    fit_h = c["decay_fit_window_h"]

    # --- 1. Наблюдения GOES ---
    obs_t, obs_v, obs_rows = {}, {}, {}
    for e in energies:
        q = f"p_ge{int(e)}"
        obs_t[e], obs_v[e], obs_rows[e] = _obs_series(sl, q, g0 - timedelta(hours=fit_h + 1), g1)
    gap_s = int(c["obs_max_gap_min"] * 60)
    J = np.full((n, len(energies)), np.nan)
    for k, e in enumerate(energies):
        J[:, k], _ = _map_samples(grid, obs_t[e], obs_v[e], gap_s, T)
    is_obs = np.isfinite(J[:, 0])
    kind = np.where(is_obs, "observation", "none").astype(object)
    conf = np.where(is_obs, "high", "none").astype(object)
    ev: list[tuple] = [("goes_obs",) if is_obs[i] else () for i in range(n)]

    t10, v10 = obs_t[energies[0]], obs_v[energies[0]]
    known = t10 <= T
    last_t = t10[known][-1] if known.any() else None
    src_state = sl.freshness(SOURCE)["state"]
    fresh = src_state in ("ok", "frozen") if sl.mode != "review" else src_state != "disabled"
    if obs_rows[energies[0]]:
        rows10 = [r for r in obs_rows[energies[0]] if to_np(r.valid_from) <= T]
        reconstructed = any(r.issued_at_policy == "reconstructed" for r in rows10)
        evidence["goes_obs"] = {
            "layer": "measurement", "kind": "observation", "source": SOURCE,
            "product": rows10[-1].product if rows10 else None,
            "quantity": "интегральный поток протонов ≥10…≥500 МэВ (геостационар)", "unit": "pfu",
            "from": iso(from_np(t10[0])) if len(t10) else None,
            "to": iso(from_np(last_t)) if last_t is not None else None,
            "reconstructed": reconstructed,
            "note": "архив NCEI L2: интегрирование дифференциальных каналов, время публикации восстановлено"
            if reconstructed else None,
            "raw_ref": {"raw_ids": sorted({r.raw_id for r in rows10})[:20]},
        }
    last_spec = None
    if last_t is not None:
        last_spec = np.array([obs_v[e][np.searchsorted(obs_t[e], last_t, side="right") - 1]
                              if len(obs_t[e]) and obs_t[e][0] <= last_t else np.nan for e in energies])
        age_h = float((T - last_t) / np.timedelta64(1, "h"))
        evidence["goes_last"] = {
            "layer": "measurement", "kind": "observation", "source": SOURCE, "time": iso(from_np(last_t)),
            "values_pfu": {f"≥{int(e)} МэВ": (None if not np.isfinite(v) else round(float(v), 4))
                           for e, v in zip(energies, last_spec)},
            "age_at_cutoff_h": round(age_h, 2), "fresh": bool(fresh)}

    # --- 2. Сообщения SWPC: предупреждения (прогноз) и идущие события (наблюдение) ---
    msgs = sl.messages(("WARPX", "WARPC", "ALTPX", "ALTPC", "SUMPX", "SUMPC"), g0 - timedelta(days=5))
    warn10 = _warning_intervals(msgs, ("WARPX1",))
    warn100 = _warning_intervals(msgs, ("WARPC0",))
    for w in warn10 + warn100:
        for m in w["messages"]:
            k, item = _msg_evidence(m)
            evidence[k] = item

    # Пропуски наблюдений внутри идущего события (по алертам) — нижняя оценка 10 pfu
    events10 = []
    for m in msgs:
        if m.code.startswith("ALTPX") and m.begin_time:
            end = next((s.end_time for s in msgs if s.code.startswith("SUMPX") and s.begin_time
                        and abs((s.begin_time - m.begin_time).total_seconds()) <= 3600 and s.end_time), None)
            events10.append((m, m.begin_time, end))
    gamma = c["default_spectrum_gamma"]
    default_shape = (np.array(energies) / energies[0]) ** (-gamma)
    # событие ≥10 МэВ, идущее на момент отсечки (по алертам и сводкам, опубликованным до T)
    active_event = next((m for m, b, e_end in reversed(events10)
                         if m.code == "ALTPX1" and to_np(b) <= T and (e_end is None or to_np(e_end) > T)), None)
    for m, b, e_end in events10:
        mask = (~is_obs) & (grid >= to_np(b)) & (grid <= T)
        if e_end is not None:
            mask &= grid <= to_np(e_end)
        if mask.any():
            k, item = _msg_evidence(m)
            evidence[k] = item
            level = 10.0 ** int(m.code[-1]) if m.code[-1].isdigit() and m.code[-1] != "0" else 10.0
            J[mask] = np.fmax(np.nan_to_num(J[mask], nan=0.0), level * default_shape)
            kind[mask] = "observation"
            conf[mask] = "medium"
            for i in np.nonzero(mask)[0]:
                ev[i] = (k,)

    # --- 3. Прогноз после отсечки ---
    fut = grid > T
    horizon = np.timedelta64(int(c["forecast_horizon_h"] * 3600), "s")
    if last_t is not None and fresh and last_spec is not None and np.isfinite(last_spec[0]):
        k_rate = _decay_rate(t10, v10, last_t, fit_h)
        h = (grid - last_t) / np.timedelta64(1, "h")
        if model == "persistence":
            use = fut
            factor = np.ones(n)
            fkind, fkey = "forecast_baseline", "baseline_persistence"
            evidence[fkey] = {"layer": "forecast", "kind": "forecast_baseline", "source": "team",
                              "model": "последнее наблюдение сохраняется", "base_time": iso(from_np(last_t))}
        elif model == "team":
            use = fut & ((grid - last_t) <= horizon)
            rate, cap, model_text = 0.0, None, "сохранение текущего значения"
            k_short = _decay_rate(t10, v10, last_t, 1.0)
            if k_rate is not None and last_spec[0] >= 1.0 and k_rate < 0:
                rate = max(k_rate, -c["max_decay_per_h"])
                model_text = "экспоненциальный спад"
            elif (k_rate is not None and k_short is not None and last_spec[0] >= 1.0
                  and k_rate > c["rising_min_rate_per_h"] and k_short > c["rising_min_rate_per_h"]):
                rate, cap = min(k_rate, k_short), float(c["max_growth_factor"])
                model_text = f"нарастание события, рост ограничен ×{cap:g}"
            factor = np.exp(rate * np.clip(h, 0, None))
            if cap is not None:
                factor = np.minimum(factor, cap)
            fkind, fkey = "forecast_team", "team_forecast"
            evidence[fkey] = {
                "layer": "forecast", "kind": "forecast_team", "source": "team", "model": model_text,
                "ln_rate_per_h": round(rate, 4), "fitted_rate_per_h": None if k_rate is None else round(k_rate, 4),
                "growth_cap": cap, "fit_window_h": fit_h, "horizon_h": c["forecast_horizon_h"],
                "base_time": iso(from_np(last_t))}
        else:  # swpc_only — базовый подход «только готовые предупреждения»
            use = np.zeros(n, dtype=bool)
            factor = np.ones(n)
            fkind, fkey = None, None
        if use.any():
            J[use] = last_spec[None, :] * factor[use, None]
            kind[use] = fkind
            hh = h[use]
            conf[use] = np.where(hh <= 3, "medium", "low") if model == "team" else "low"
            for i in np.nonzero(use)[0]:
                ev[i] = (fkey, "goes_last")
            # Событие официально продолжается (алерт SWPC есть, сводки об окончании до T нет) —
            # прогноз не опускается ниже порога S1
            if model == "team" and active_event is not None:
                ak = f"msg:{active_event.code}:{active_event.serial}"
                evidence[ak] = _msg_evidence(active_event)[1]
                for i in np.nonzero(use)[0]:
                    if J[i, 0] < c["geo_s1_pfu"]:
                        J[i] = J[i] * (c["geo_s1_pfu"] / J[i, 0]) if J[i, 0] > 0 else c["geo_s1_pfu"] * default_shape
                        ev[i] = ev[i] + (ak,)
    elif fut.any():
        notes.append("Измерения потока протонов отсутствуют или устарели на момент отсечки — "
                     "собственный прогноз не строится.")

    # Предупреждения SWPC: поток не ниже порога предупреждения
    for w, e_idx, floor in [(w, 0, c["geo_s1_pfu"]) for w in warn10] + [(w, energies.index(100.0), 1.0) for w in warn100]:
        m = fut & (grid >= to_np(w["valid_from"])) & (grid <= to_np(w["valid_to"]))
        if not m.any():
            continue
        keys = tuple(f"msg:{x.code}:{x.serial}" for x in w["messages"][-1:])
        for i in np.nonzero(m)[0]:
            row = J[i]
            if np.isfinite(row[e_idx]) and row[e_idx] >= floor:
                ev[i] = ev[i] + keys
                continue
            shape = row / row[e_idx] if np.isfinite(row).all() and row[e_idx] > 0 else default_shape / default_shape[e_idx]
            J[i] = np.maximum(np.nan_to_num(row, nan=0.0), floor * shape)
            kind[i] = "forecast_external"
            conf[i] = "medium"
            ev[i] = keys + tuple(x for x in ev[i] if x != "goes_obs")

    # --- 4. Kp: наблюдение -> прогноз SWPC -> сохранение -> консервативное допущение ---
    kp_obs_rows = sl.records("kp_observed", "kp", g0 - timedelta(hours=3), g1, "observation")
    kp, kp_src = _map_intervals(grid, kp_obs_rows)
    kp_kind = np.where(np.isfinite(kp), "observation", "none").astype(object)
    kp_fc_rows = sl.latest_forecasts(["swpc_3day", "kp_forecast"], "kp", g0, g1)
    kp_fc, kp_fc_src = _map_intervals(grid, kp_fc_rows)
    # без наблюдения: в пределах 6 ч после последнего — максимум из сохранения и прогноза SWPC
    # (консервативно: прогноз, выпущенный до прихода бури, не должен занижать текущую обстановку)
    pers_val = np.full(n, np.nan)
    if kp_obs_rows:
        last_kp = max(kp_obs_rows, key=lambda r: r.valid_to)
        pers = (grid >= to_np(last_kp.valid_to)) & (grid < to_np(last_kp.valid_to) + horizon)
        pers_val[pers] = last_kp.value
    missing = ~np.isfinite(kp)
    use_pers = missing & np.isfinite(pers_val) & ~(np.isfinite(kp_fc) & (kp_fc > pers_val))
    use_fc = missing & np.isfinite(kp_fc) & ~use_pers
    kp[use_pers] = pers_val[use_pers]
    kp_kind[use_pers] = "persistence"
    kp[use_fc] = kp_fc[use_fc]
    kp_kind[use_fc] = "forecast_external"
    assumed = ~np.isfinite(kp)
    kp[assumed] = c["kp_assumed_if_unknown"]
    kp_kind[assumed] = "assumed"
    if assumed.any():
        notes.append(f"Kp неизвестен на части интервала — принят консервативно Kp={c['kp_assumed_if_unknown']}.")
    issued = sorted({r.issued_at for r in kp_fc_rows})
    evidence["kp"] = {
        "layer": "measurement", "kind": "observation+forecast", "source": "kp_observed / swpc_3day / kp_forecast",
        "observed_until": iso(max((r.valid_to for r in kp_obs_rows), default=None)),
        "observed_reconstructed": any(r.issued_at_policy == "reconstructed" for r in kp_obs_rows),
        "forecast_issues": [iso(x) for x in issued[-3:]],
        "max_kp_in_interval": round(float(np.nanmax(kp)), 2) if n else None,
        "assumed_points": int(assumed.sum()),
        "role": "Kp не отдельный риск: сдвигает границу проникновения частиц (модификатор Rc)"}

    # --- 5. Геометрия: Rc, энергия обрезания, ЮАА ---
    rc = orbit.rc_with_kp(track.mlat, track.r_re, kp, c["kp_cutoff_shift_deg_per_kp"])
    e_cut = orbit.rigidity_to_energy(rc)
    saa = track.b_nt < c["saa_b_nt"]
    evidence["geometry"] = {
        "layer": "condition", "kind": "geometry", "source": "team",
        "model": "Rc = 14.9·cos⁴(λ_eff)/r² ГВ, λ_eff = |λ_m| + сдвиг·Kp; ЮАА: |B| IGRF-14 ниже порога",
        "kp_shift_deg_per_kp": c["kp_cutoff_shift_deg_per_kp"], "saa_b_nt": c["saa_b_nt"],
        "note": "центрированный диполь — приближение; J_iss — показатель обстановки, не доза"}
    j_iss = integral_above(e_cut, energies, J)

    # --- 6. Вероятности на сутки (за горизонтом собственного прогноза) ---
    prob_rows = sl.latest_forecasts(["swpc_3day"], "prob_s1", g0, g1) + \
        sl.latest_forecasts(["swpc_rsga"], "prob_proton", g0, g1)
    prob = np.full(n, np.nan)
    prob_src: list = [None] * n
    for r in sorted(prob_rows, key=lambda x: x.issued_at):  # более свежий выпуск перезаписывает
        m = (grid >= to_np(r.valid_from)) & (grid < to_np(r.valid_to))
        prob[m] = r.value
        for i in np.nonzero(m)[0]:
            prob_src[i] = r
    for r in {id(x): x for x in prob_src if x is not None}.values():
        evidence[f"prob:{r.source}:{r.issued_at:%Y%m%dT%H%M}"] = {
            "layer": "forecast", "kind": "probability", "source": r.source, "quantity": r.quantity,
            "issued_at": iso(r.issued_at), "raw_ref": {"raw_id": r.raw_id, "locator": r.locator}}

    # --- 7. Классы ---
    cls = np.full(n, NO_DATA, dtype=np.int8)
    reason = np.full(n, "no_observation", dtype=object)
    has_flux = np.isfinite(J[:, 0])
    p10 = J[:, 0]
    for i in range(n):
        if has_flux[i]:
            if p10[i] >= c["geo_s3_pfu"]:
                cls[i], reason[i] = CRITICAL, "sep_s3"
            elif j_iss[i] >= c["jiss_critical_pfu"]:
                cls[i], reason[i] = CRITICAL, "sep_open_zone"
            elif j_iss[i] >= c["jiss_undesirable_pfu"]:
                cls[i], reason[i] = UNDESIRABLE, "sep_partial_access"
            elif p10[i] >= c["geo_s1_pfu"]:
                cls[i], reason[i] = UNDESIRABLE, "sep_shielded"
            elif saa[i]:
                cls[i], reason[i] = UNDESIRABLE, "saa"
            else:
                cls[i], reason[i] = ACCEPTABLE, "nominal"
            continue
        if not fut[i]:
            if src_state == "disabled":
                reason[i] = "source_disabled"
            continue  # прошлое без измерений — «нет данных»
        if not fresh and model != "swpc_only":
            reason[i] = "source_disabled" if src_state == "disabled" else "obs_stale"
            continue
        ongoing = (last_spec is not None and np.isfinite(last_spec[0]) and last_spec[0] >= c["geo_s1_pfu"]) \
            or active_event is not None
        if ongoing and model == "team":
            cls[i], reason[i] = UNDESIRABLE, "sep_ongoing_beyond_horizon"
            kind[i], conf[i], ev[i] = "forecast_team", "low", ("goes_last",)
        elif np.isfinite(prob[i]):
            r = prob_src[i]
            key = f"prob:{r.source}:{r.issued_at:%Y%m%dT%H%M}"
            if prob[i] >= c["prob_undesirable_pct"]:
                cls[i], reason[i] = UNDESIRABLE, "sep_probable"
            else:
                cls[i], reason[i] = ACCEPTABLE, "sep_unlikely"
            kind[i], conf[i], ev[i] = "probability", "low", (key,)
        elif model == "swpc_only":
            cls[i], reason[i] = ACCEPTABLE, "nominal"
            kind[i], conf[i] = "forecast_baseline", "low"
        else:
            reason[i] = "no_forecast"
            continue
        if cls[i] == ACCEPTABLE and saa[i]:
            cls[i], reason[i] = UNDESIRABLE, "saa"
    # геометрия и Kp участвуют во всех оценках с измеренным/прогнозным потоком
    for i in range(n):
        if cls[i] != NO_DATA:
            ev[i] = tuple(dict.fromkeys(ev[i] + ("geometry", "kp")))
            if kp_kind[i] == "assumed" and conf[i] in ("high", "medium"):
                conf[i] = "low"

    series = {"p_ge10": J[:, 0], "p_ge100": J[:, energies.index(100.0)], "j_iss": j_iss, "kp": kp,
              "rc_gv": rc, "e_cut_mev": e_cut, "saa": saa.astype(float), "prob_sep": prob,
              "kp_assumed": (kp_kind == "assumed").astype(float)}
    return MechanismTimeline(name="radiation", times=grid, cls=cls, reason=reason, kind=kind, confidence=conf,
                             evidence=ev, evidence_items=evidence, series=series, notes=notes,
                             events=[{"code": m.code, "serial": m.serial, "begin": iso(b), "end": iso(e)}
                                     for m, b, e in events10])
