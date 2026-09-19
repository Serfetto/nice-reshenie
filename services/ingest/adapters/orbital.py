"""Орбитальные элементы: МКС и объекты каталога на высотах МКС.

Space-Track: текущие (class gp) и исторические (class gp_history) наборы с CREATION_DATE —
временем публикации, по которому строится срез «на момент T».
CelesTrak: резервный источник TLE МКС (время публикации неизвестно — берём время получения).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote

import httpx
from sgp4.api import Satrec

from common import config
from common.db import elements, insert_ignore
from common.timeutil import parse_iso, utcnow
from services.ingest import http
from services.ingest.adapters.base import Adapter, IngestResult, days_with_data, gap_start, last_time

ISS_NORAD = 25544
FIELDS = "NORAD_CAT_ID,OBJECT_NAME,OBJECT_TYPE,EPOCH,CREATION_DATE,PERIAPSIS,APOAPSIS,TLE_LINE1,TLE_LINE2"
EARTH_R = 6378.137
MU = 398600.4418


class SpaceTrackSession:
    """Одна сессия на процесс: вход по логину, соблюдение ограничения частоты запросов."""
    _lock = threading.Lock()
    _client: httpx.Client | None = None
    _last_request = 0.0

    @classmethod
    def get(cls, path: str) -> http.Response:
        st = config.sources()["spacetrack"]
        user, password = config.secret("SPACETRACK_USER"), config.secret("SPACETRACK_PASSWORD")
        if not user or not password:
            raise http.FetchError("Нет SPACETRACK_USER / SPACETRACK_PASSWORD в .env")
        with cls._lock:
            for attempt in range(2):
                if cls._client is None:
                    cls._client = httpx.Client(timeout=120, follow_redirects=True,
                                               headers={"User-Agent": http.USER_AGENT})
                    cls._throttle(st)
                    r = cls._client.post(st["base_url"] + "/ajaxauth/login",
                                         data={"identity": user, "password": password})
                    if r.status_code != 200 or "Failed" in r.text:
                        cls._client = None
                        raise http.FetchError(f"Space-Track: вход не выполнен (HTTP {r.status_code})", r.status_code)
                cls._throttle(st)
                url = st["base_url"] + path
                r = cls._client.get(url)
                if r.status_code == 401 and attempt == 0:
                    cls._client = None
                    continue
                if r.status_code >= 400:
                    raise http.FetchError(f"Space-Track HTTP {r.status_code}: {r.text[:200]}", r.status_code)
                return http.Response(url, r.status_code, r.content, r.headers.get("content-type"))
        raise http.FetchError("Space-Track: не удалось выполнить запрос")

    @classmethod
    def _throttle(cls, st: dict) -> None:
        wait = st.get("min_request_interval_s", 2.5) - (time.monotonic() - cls._last_request)
        if wait > 0:
            time.sleep(wait)
        cls._last_request = time.monotonic()


def _q(path: str) -> str:
    return quote(path, safe="/,-:.")


def _rows_from_gp(items: list[dict], raw_id: int, fetched_at: datetime) -> list[dict]:
    rows = []
    for it in items:
        if not it.get("TLE_LINE1") or not it.get("TLE_LINE2"):
            continue
        rows.append(dict(
            norad_id=int(it["NORAD_CAT_ID"]), object_name=it.get("OBJECT_NAME"), object_type=it.get("OBJECT_TYPE"),
            epoch=parse_iso(it["EPOCH"]), creation_date=parse_iso(it["CREATION_DATE"]), creation_policy="exact",
            line1=it["TLE_LINE1"], line2=it["TLE_LINE2"],
            periapsis=float(it["PERIAPSIS"]) if it.get("PERIAPSIS") not in (None, "") else None,
            apoapsis=float(it["APOAPSIS"]) if it.get("APOAPSIS") not in (None, "") else None,
            source="spacetrack", fetched_at=fetched_at, raw_id=raw_id))
    return rows


class _SpaceTrackAdapter(Adapter):
    def parse(self, conn, raw_id: int, content: bytes, url: str, fetched_at: datetime) -> int:
        return insert_ignore(conn, elements, _rows_from_gp(json.loads(content), raw_id, fetched_at))

    def _fetch(self, engine, path: str, result: IngestResult) -> None:
        resp = SpaceTrackSession.get(_q(path))
        self.store_response(engine, resp.url, resp, result)


class IssSpaceTrack(_SpaceTrackAdapter):
    name = "iss_spacetrack"
    has_backfill = True

    def live(self, engine) -> IngestResult:
        """Текущий набор; если последний опубликованный старше суток (простой) — история за пропуск одним запросом."""
        result = IngestResult(self.name)
        now = utcnow()
        last = last_time(engine, elements.c.creation_date, elements.c.norad_id == ISS_NORAD,
                         elements.c.source == "spacetrack")
        self._fetch(engine, f"/basicspacedata/query/class/gp/NORAD_CAT_ID/{ISS_NORAD}/format/json", result)
        if last is None or now - last > timedelta(days=1):
            self._fill(result, "истории Space-Track", lambda: self.backfill(engine, gap_start(last, now, 7), now.date(),
                                                                            pad_days=0))
        return result

    def covered_days(self, conn, start: date, end: date) -> set[date]:
        return days_with_data(conn, elements.c.epoch, [elements.c.norad_id == ISS_NORAD,
                                                       elements.c.source == "spacetrack"], start, end)

    def backfill(self, engine, start: date, end: date, pad_days: int = 3) -> IngestResult:
        result = IngestResult(self.name)
        a, b = start - timedelta(days=pad_days), end + timedelta(days=1)
        self._fetch(engine, f"/basicspacedata/query/class/gp_history/NORAD_CAT_ID/{ISS_NORAD}"
                            f"/EPOCH/{a:%Y-%m-%d}--{b:%Y-%m-%d}/orderby/EPOCH asc/format/json", result)
        return result


class CatalogSpaceTrack(_SpaceTrackAdapter):
    """Объекты, чей перигей и апогей пересекают полосу высот МКС."""
    name = "catalog_spacetrack"
    has_backfill = True

    def _band(self) -> tuple[float, float]:
        st = config.sources()["spacetrack"]
        return st["band_min_km"], st["band_max_km"]

    def live(self, engine) -> IngestResult:
        """Текущие элементы; после простоя дольше суток — история за пропуск (не глубже недели: по суткам, тяжело)."""
        lo, hi = self._band()
        result = IngestResult(self.name)
        now = utcnow()
        last = last_time(engine, elements.c.creation_date, elements.c.norad_id != ISS_NORAD,
                         elements.c.source == "spacetrack")
        self._fetch(engine, f"/basicspacedata/query/class/gp/PERIAPSIS/<{hi:.0f}/APOAPSIS/>{lo:.0f}"
                            f"/DECAY_DATE/null-val/predicates/{FIELDS}/format/json", result)
        if last is not None and now - last > timedelta(days=1):
            start = max(gap_start(last, now, 0), (now - timedelta(days=7)).date())
            self._fill(result, "истории Space-Track", lambda: self.backfill(engine, start, now.date(), pad_days=0))
        return result

    def covered_days(self, conn, start: date, end: date) -> set[date]:
        return days_with_data(conn, elements.c.epoch, [elements.c.norad_id != ISS_NORAD,
                                                       elements.c.source == "spacetrack"], start, end, min_count=100)

    def backfill(self, engine, start: date, end: date, pad_days: int = 3) -> IngestResult:
        """По суткам эпохи. Фильтр по дате схода не ставим: объекты, сошедшие после 2024 г., тогда летали.
        pad_days — запас до начала: элементы с эпохой до трёх суток раньше нужны для первого дня."""
        lo, hi = self._band()
        result = IngestResult(self.name)
        d = start - timedelta(days=pad_days)
        while d <= end:
            try:
                self._fetch(engine, f"/basicspacedata/query/class/gp_history/EPOCH/{d:%Y-%m-%d}--"
                                    f"{d + timedelta(days=1):%Y-%m-%d}/PERIAPSIS/<{hi:.0f}/APOAPSIS/>{lo:.0f}"
                                    f"/predicates/{FIELDS}/format/json", result)
            except Exception as e:  # noqa: BLE001 - keep other archive days usable after one failure
                result.errors.append(f"{d}: {e}")
            d += timedelta(days=1)
        return result


def tle_epoch(line1: str, line2: str) -> datetime:
    sat = Satrec.twoline2rv(line1, line2)
    jd = sat.jdsatepoch + sat.jdsatepochF
    return (datetime(2000, 1, 1, 12) + timedelta(days=jd - 2451545.0)).replace(microsecond=0)


def tle_apsides(line2: str) -> tuple[float, float]:
    ecc = float("0." + line2[26:33].strip())
    n = float(line2[52:63]) * 2 * 3.141592653589793 / 86400
    a = (MU / n ** 2) ** (1 / 3)
    return a * (1 - ecc) - EARTH_R, a * (1 + ecc) - EARTH_R


class IssCelestrak(Adapter):
    name = "iss_celestrak"

    def live(self, engine) -> IngestResult:
        result = IngestResult(self.name)
        self.fetch_and_store(engine, self.cfg["live_url"], result)
        return result

    def parse(self, conn, raw_id: int, content: bytes, url: str, fetched_at: datetime) -> int:
        lines = [ln.rstrip() for ln in content.decode("ascii", errors="replace").splitlines() if ln.strip()]
        rows = []
        for i, ln in enumerate(lines):
            if ln.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
                l1, l2 = ln, lines[i + 1]
                name = lines[i - 1].strip() if i > 0 and not lines[i - 1].startswith(("1 ", "2 ")) else None
                per, apo = tle_apsides(l2)
                rows.append(dict(norad_id=int(l1[2:7]), object_name=name, object_type="PAYLOAD",
                                 epoch=tle_epoch(l1, l2), creation_date=fetched_at, creation_policy="fetched",
                                 line1=l1, line2=l2, periapsis=per, apoapsis=apo, source="celestrak",
                                 fetched_at=fetched_at, raw_id=raw_id))
        return insert_ignore(conn, elements, rows)
