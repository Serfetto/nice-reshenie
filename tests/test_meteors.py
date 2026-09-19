"""Метеорные потоки: разбор прогноза NASA и экранирование радианта Землёй."""
from datetime import datetime

import numpy as np

from services.assessment.core.meteors import radiant_visible
from services.assessment.core.mmod import impact_physics
from services.ingest.adapters.meteors import parse_flux, parse_radiants

FLUX = """# 2024 meteor shower forecast for low Earth orbit
# Units of flux are number per square meter per hour
#
#   UT date and time     Julian date    solar     zhr          flux          flux          flux          flux        factor        factor        factor        factor
#                                         lon            6.70e+00 J    1.05e+02 J    2.83e+03 J    1.05e+05 J    6.70e+00 J    1.05e+02 J    2.83e+03 J    1.05e+05 J
 2024-06-09 23:00:00  2460471.458333   79.460   50.12  1.574e-06  1.023e-07  3.865e-09  1.198e-10  3.300e-01  5.820e-01  1.496e+00  4.839e+00
"""
DOC = "<p>eta Aquariids 338 -1 66 2024-05-05 13:53\nDaytime Arietids 42 +24 39 2024-06-09 22:39\n</p>"


def test_parse_flux_and_radiants():
    labels, rows = parse_flux(FLUX)
    assert labels == ["6.7J", "105J", "2830J", "105000J"]
    t, flux, factor = rows[0]
    assert t == datetime(2024, 6, 9, 23) and factor[0] == 0.33 and flux[3] == 1.198e-10
    rad = parse_radiants(DOC)
    assert [(r["name"], r["ra"], r["dec"], r["speed_km_s"]) for r in rad] == [
        ("eta Aquariids", 338.0, -1.0, 66.0), ("Daytime Arietids", 42.0, 24.0, 39.0)]


def test_radiant_shielded_by_earth():
    r = np.array([[6800.0, 0.0, 0.0]] * 3)
    v = np.array([[0.0, 7.66, 0.0]] * 3)
    visible = [radiant_visible(r[:1], v[:1], np.array(s), 60.0)[0]
               for s in ([1.0, 0, 0], [-1.0, 0, 0], [0, 0, 1.0])]
    assert visible == [True, False, True]  # зенит — виден, надир — закрыт Землёй, горизонт — виден


def test_impact_energy():
    p = impact_physics(14.6, 150.0)
    assert p["pass_type"] == "встречный" and abs(p["energy_per_gram_kj"] - 106.6) < 0.1
