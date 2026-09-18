"""Физика и геометрия: энергия обрезания, спектр, координаты."""
import numpy as np

from services.assessment.core import orbit
from services.assessment.core.radiation import integral_above


def test_rigidity_energy():
    # 100 МэВ протон ~ 0.444 ГВ, 10 МэВ ~ 0.137 ГВ
    assert abs(orbit.rigidity_to_energy(0.4446) - 100) < 1
    assert abs(orbit.rigidity_to_energy(0.1373) - 10) < 0.5


def test_stormer_equator_and_pole():
    assert abs(orbit.stormer_rc(np.array([0.0]), np.array([1.0]))[0] - 14.9) < 1e-9
    assert orbit.stormer_rc(np.array([89.0]), np.array([1.0]))[0] < 1e-4


def test_kp_lowers_cutoff():
    mlat, r = np.array([55.0]), np.array([1.066])
    quiet = orbit.rc_with_kp(mlat, r, np.array([0.0]), 1.0)[0]
    storm = orbit.rc_with_kp(mlat, r, np.array([9.0]), 1.0)[0]
    assert storm < quiet


def test_geodetic_roundtrip():
    # точка на экваторе на высоте 420 км
    p = np.array([[orbit.EARTH_R_KM + 420.0, 0.0, 0.0]])
    lat, lon, h = orbit.ecef_to_geodetic(p)
    assert abs(lat[0]) < 1e-6 and abs(lon[0]) < 1e-6 and abs(h[0] - 420) < 1e-6


def test_integral_interpolation():
    e = [10, 30, 50, 100, 500]
    j = np.array([[100.0, 30.0, 15.0, 5.0, 0.5]])
    # на узле — значение узла; между узлами — между соседями; ниже 10 МэВ — J(>10)
    assert abs(integral_above(np.array([100.0]), e, j)[0] - 5.0) < 1e-9
    v = integral_above(np.array([70.0]), e, j)[0]
    assert 5.0 < v < 15.0
    assert abs(integral_above(np.array([3.0]), e, j)[0] - 100.0) < 1e-9
    assert integral_above(np.array([1000.0]), e, j)[0] < 0.5


def test_integral_monotonic_with_background():
    # фон ГКЛ: поток на высоких энергиях не может превышать поток на низких
    e = [10, 30, 50, 100, 500]
    j = np.array([[0.25, 0.22, 0.21, 0.21, 0.24]])
    v = integral_above(np.array([600.0]), e, j)[0]
    assert v <= 0.25
