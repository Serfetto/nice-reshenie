"""Орбита МКС и её геомагнитные атрибуты.

SGP4 (TEME) -> вращение на гринвичское звёздное время -> ITRF -> геодезические координаты (WGS84).
Магнитное поле: IGRF-14 (ppigrf) для |B| и флага ЮАА; центрированный диполь IGRF для
геомагнитной широты, оболочки L и жёсткости обрезания Штёрмера Rc = 14.9·cos⁴λ / r² ГВ.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

import numpy as np
from sgp4.api import Satrec, SatrecArray

EARTH_R_KM = 6378.137
MEAN_R_KM = 6371.2
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)
PROTON_MASS_MEV = 938.272
STORMER_GV = 14.9


def satrec(line1: str, line2: str) -> Satrec:
    return Satrec.twoline2rv(line1, line2)


def julian(times: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """datetime64[s] -> (jd, fr) для sgp4."""
    sec = times.astype("datetime64[s]").astype(np.int64).astype(float)
    days = sec / 86400.0
    jd_full = 2440587.5 + days
    jd = np.floor(jd_full - 0.5) + 0.5
    return jd, jd_full - jd


def propagate(sat: Satrec, times: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    jd, fr = julian(times)
    e, r, v = sat.sgp4_array(jd, fr)
    return np.asarray(r), np.asarray(v), np.asarray(e)


def propagate_many(sats: list[Satrec], times: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(n_sat, n_t, 3) положения и скорости в TEME, коды ошибок (n_sat, n_t)."""
    jd, fr = julian(times)
    e, r, v = SatrecArray(sats).sgp4(jd, fr)
    return r, v, e


def gmst_rad(times: np.ndarray) -> np.ndarray:
    jd, fr = julian(times)
    t = ((jd - 2451545.0) + fr) / 36525.0
    gmst = (67310.54841 + (876600.0 * 3600.0 + 8640184.812866) * t + 0.093104 * t ** 2
            - 6.2e-6 * t ** 3)
    return np.deg2rad((gmst % 86400.0) / 240.0)


def teme_to_ecef(r: np.ndarray, times: np.ndarray) -> np.ndarray:
    th = gmst_rad(times)
    c, s = np.cos(th), np.sin(th)
    x = c * r[..., 0] + s * r[..., 1]
    y = -s * r[..., 0] + c * r[..., 1]
    return np.stack([x, y, r[..., 2]], axis=-1)


def ecef_to_geodetic(p: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x, y, z = p[..., 0], p[..., 1], p[..., 2]
    lon = np.arctan2(y, x)
    rho = np.hypot(x, y)
    lat = np.arctan2(z, rho * (1 - WGS84_E2))
    for _ in range(6):
        n = EARTH_R_KM / np.sqrt(1 - WGS84_E2 * np.sin(lat) ** 2)
        h = rho / np.cos(lat) - n
        lat = np.arctan2(z, rho * (1 - WGS84_E2 * n / (n + h)))
    n = EARTH_R_KM / np.sqrt(1 - WGS84_E2 * np.sin(lat) ** 2)
    h = rho / np.cos(lat) - n
    return np.rad2deg(lat), np.rad2deg(lon), h


@lru_cache(maxsize=8)
def dipole_pole(year: int, month: int) -> np.ndarray:
    """Единичный вектор на северный геомагнитный полюс (ECEF) по коэффициентам g10, g11, h11 IGRF."""
    import pandas as pd
    import ppigrf.ppigrf as P

    g, h = P.read_shc(P.shc_fn)
    when = pd.Timestamp(year=year, month=month, day=15)
    gi = g.reindex(g.index.union([when])).interpolate(method="time").loc[when]
    hi = h.reindex(h.index.union([when])).interpolate(method="time").loc[when]
    g10, g11, h11 = float(gi[(1, 0)]), float(gi[(1, 1)]), float(hi[(1, 1)])
    b0 = np.sqrt(g10 ** 2 + g11 ** 2 + h11 ** 2)
    theta0 = np.arccos(-g10 / b0)
    phi0 = np.arctan2(-h11, -g11)
    return np.array([np.sin(theta0) * np.cos(phi0), np.sin(theta0) * np.sin(phi0), np.cos(theta0)])


def field_magnitude(lat: np.ndarray, lon: np.ndarray, alt: np.ndarray, when: datetime) -> np.ndarray:
    import ppigrf

    be, bn, bu = ppigrf.igrf(lon, lat, alt, when)
    return np.sqrt(be ** 2 + bn ** 2 + bu ** 2).reshape(-1)


def sun_unit_eci(times: np.ndarray) -> np.ndarray:
    """Направление на Солнце (экваториальная система даты, точность ~0.01°)."""
    jd, fr = julian(times)
    n = (jd - 2451545.0) + fr
    L = np.deg2rad((280.460 + 0.9856474 * n) % 360)
    g = np.deg2rad((357.528 + 0.9856003 * n) % 360)
    lam = L + np.deg2rad(1.915) * np.sin(g) + np.deg2rad(0.020) * np.sin(2 * g)
    eps = np.deg2rad(23.439 - 4e-7 * n)
    return np.stack([np.cos(lam), np.cos(eps) * np.sin(lam), np.sin(eps) * np.sin(lam)], axis=-1)


def sunlit(r_teme: np.ndarray, times: np.ndarray) -> np.ndarray:
    """Цилиндрическая модель тени Земли."""
    s = sun_unit_eci(times)
    proj = np.sum(r_teme * s, axis=-1)
    perp = np.linalg.norm(r_teme - proj[..., None] * s, axis=-1)
    return ~((proj < 0) & (perp < EARTH_R_KM))


def stormer_rc(mlat_deg: np.ndarray, r_re: np.ndarray) -> np.ndarray:
    return STORMER_GV * np.cos(np.deg2rad(mlat_deg)) ** 4 / r_re ** 2


def rc_with_kp(mlat_deg: np.ndarray, r_re: np.ndarray, kp: np.ndarray, shift_deg_per_kp: float) -> np.ndarray:
    """Буря сдвигает границу проникновения частиц к экватору: эффективная широта растёт с Kp."""
    eff = np.clip(np.abs(mlat_deg) + shift_deg_per_kp * np.nan_to_num(kp, nan=0.0), 0, 89.9)
    return stormer_rc(eff, r_re)


def rigidity_to_energy(r_gv: np.ndarray) -> np.ndarray:
    """Жёсткость протона (ГВ) -> кинетическая энергия (МэВ)."""
    pc = np.asarray(r_gv) * 1000.0
    return np.sqrt(pc ** 2 + PROTON_MASS_MEV ** 2) - PROTON_MASS_MEV


@dataclass
class Track:
    times: np.ndarray       # datetime64[s]
    r_teme: np.ndarray      # км
    v_teme: np.ndarray      # км/с
    lat: np.ndarray         # град, геодезическая
    lon: np.ndarray
    alt: np.ndarray         # км
    r_re: np.ndarray        # геоцентрическое расстояние в радиусах Земли
    mlat: np.ndarray        # геомагнитная широта (центрированный диполь), град
    b_nt: np.ndarray        # |B| IGRF, нТл
    sunlit: np.ndarray      # bool
    error: np.ndarray       # код ошибки SGP4 (0 — норма)


def build_track(sat: Satrec, times: np.ndarray, when: datetime) -> Track:
    r, v, e = propagate(sat, times)
    ecef = teme_to_ecef(r, times)
    lat, lon, alt = ecef_to_geodetic(ecef)
    r_norm = np.linalg.norm(ecef, axis=-1)
    pole = dipole_pole(when.year, when.month)
    mlat = np.rad2deg(np.arcsin(np.clip(ecef @ pole / r_norm, -1, 1)))
    b = field_magnitude(lat, lon, alt, when)
    return Track(times=times, r_teme=r, v_teme=v, lat=lat, lon=lon, alt=alt, r_re=r_norm / MEAN_R_KM,
                 mlat=mlat, b_nt=b, sunlit=sunlit(r, times), error=e)
