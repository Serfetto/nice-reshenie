"""Отбор сближений: объекты, летящие вместе с МКС, не дают ложных сближений."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np

from common import config
from common.timeutil import time_grid
from services.assessment.core import orbit
from services.assessment.core.mmod import assess_mmod, co_orbiting

ISS_L1 = "1 25544U 98067A   24128.06194722  .00013194  00000-0  23083-3 0  9995"
ISS_L2 = "2 25544  51.6397 159.2957 0003628 145.8725  64.6623 15.50961316452164"
EPOCH = datetime(2024, 5, 7, 1, 29, 12)


def _with(line2: str, start: int, end: int, value: str) -> str:
    return line2[:start] + value.rjust(end - start) + line2[end:]


# «Пристыкованный» объект: тот же TLE, сдвиг по средней аномалии 0.1° — около 12 км вдоль орбиты,
# как у кораблей, чей TLE отличается от TLE МКС эпохой
DOCKED_L2 = _with(ISS_L2, 43, 51, "64.7623")
# Объект на другой орбите: наклонение 97° — пересекает орбиту МКС со скоростью порядка км/с
CROSSING_L2 = _with(ISS_L2, 8, 16, "97.0000")


def _obj(norad: int, name: str, line2: str):
    l1 = ISS_L1[:2] + f"{norad:05d}" + ISS_L1[7:]
    return SimpleNamespace(norad_id=norad, object_name=name, object_type="PAYLOAD", line1=l1,
                           line2=line2[:2] + f"{norad:05d}" + line2[7:], epoch=EPOCH, creation_date=EPOCH, raw_id=1)


class FakeSlice:
    def __init__(self, objs):
        self.objs = {o.norad_id: o for o in objs}
        self.as_of = datetime(2024, 5, 7, 12)

    def freshness(self, source):
        return {"state": "ok", "latest_data": "2024-05-07T02:00:00Z"}

    def catalog_elements(self, t_ref, max_age_days, epoch_not_after=None):
        return self.objs

    def cutoff(self, source):
        return datetime(2024, 5, 8)

    def latest_forecasts(self, sources, quantity, t0, t1):
        return []

    def records(self, source, quantity, t0, t1, kind):
        return []


def _track(hours: float = 4):
    t0 = datetime(2024, 5, 7, 12)
    grid = time_grid(t0, t0 + timedelta(hours=hours), 30)
    sat = orbit.satrec(ISS_L1, ISS_L2)
    return sat, orbit.build_track(sat, grid, t0)


def test_co_orbiting_by_speed_even_when_km_apart():
    sat, track = _track()
    docked = orbit.satrec(_obj(90001, "DOCKED", DOCKED_L2).line1, _obj(90001, "DOCKED", DOCKED_L2).line2)
    r, v, e = orbit.propagate(docked, track.times)
    d = np.linalg.norm(r - track.r_teme, axis=-1)
    vrel = np.linalg.norm(v - track.v_teme, axis=-1)
    c = config.thresholds()["mmod"]
    assert np.median(d) > c["docked_median_km"]  # по одному расстоянию не отсеялся бы
    assert co_orbiting(d, vrel, c)


def test_docked_object_gives_no_conjunction():
    docked = _obj(90001, "DOCKED", DOCKED_L2)
    crossing = _obj(90002, "CROSSING", CROSSING_L2)
    sat, track = _track()
    m = assess_mmod(FakeSlice([docked, crossing]), track, sat, config.thresholds())
    assert all(ev["norad_id"] != 90001 for ev in m.events)
    assert [x["norad_id"] for x in m.evidence_items["catalog"]["excluded_coorbiting"]] == [90001]
