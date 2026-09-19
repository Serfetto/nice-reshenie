"""Интегральный поток протонов GOES.

Живой поток: SWPC JSON (5-мин значения по порогам энергии, primary GOES).
Архив: NCEI GOES-R SGPS L2 avg5m (netCDF, дифференциальные каналы). Интегральные потоки
восстанавливаются интегрированием спектра; файлы созданы после событий, поэтому
в replay это реконструкция (issued_at_policy = reconstructed).
"""
from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from common.db import insert_ignore, records
from common.timeutil import parse_iso, utcnow
from services.ingest.adapters.base import Adapter, IngestResult, days_with_data, gap_start, last_time

ENERGIES = {">=10 MeV": 10, ">=30 MeV": 30, ">=50 MeV": 50, ">=100 MeV": 100, ">=500 MeV": 500}
STEP = timedelta(minutes=5)


def quantity(e_mev: int) -> str:
    return f"p_ge{e_mev}"


def integrate_sgps(lower_kev: np.ndarray, upper_kev: np.ndarray, diff: np.ndarray, int500: np.ndarray,
                   e_mev: float) -> np.ndarray:
    """Интегральный поток выше e_mev (pfu) по дифференциальным каналам одного датчика.

    diff: (time, channels) в протон/(см² ср кэВ с). Перекрытия каналов обрезаются по верхней
    границе предыдущего. Выше последнего канала добавляется интегральный канал >500 МэВ.
    """
    e_kev = e_mev * 1000.0
    order = np.argsort(lower_kev)
    lo, hi, f = lower_kev[order].astype(float), upper_kev[order].astype(float), diff[:, order]
    for i in range(1, len(lo)):
        lo[i] = max(lo[i], hi[i - 1])
    width = np.clip(hi - np.maximum(lo, e_kev), 0, None)
    total = np.nansum(np.where(np.isfinite(f), f, 0.0) * width, axis=1)
    if e_kev <= 500_000:
        total = total + np.where(np.isfinite(int500), int500, 0.0)
    all_nan = np.all(~np.isfinite(f[:, width > 0]), axis=1) if np.any(width > 0) else ~np.isfinite(int500)
    return np.where(all_nan, np.nan, total)


class GoesProtons(Adapter):
    name = "goes_protons"
    has_backfill = True
    parser_version = "1"

    def live(self, engine) -> IngestResult:
        """Обычно — ряд за 6 ч; после простоя — продукт SWPC, покрывающий пропуск (до 7 сут), старше — архив NCEI."""
        result = IngestResult(self.name)
        now = utcnow()
        last = last_time(engine, records.c.valid_to, records.c.source == self.name,
                         records.c.product == "swpc_integral_protons")
        self.fetch_and_store(engine, self.live_url_for_gap(None if last is None else (now - last).total_seconds() / 3600),
                             result)
        if last is not None and now - last > timedelta(days=7):
            self._fill(result, "архива NCEI", lambda: self.backfill(engine, gap_start(last, now, 0),
                                                                     (now - timedelta(days=7)).date()))
        return result

    def live_url_for_gap(self, gap_h: float | None) -> str:
        """Самый короткий продукт, который покрывает пропуск с запасом в час; данных нет — самый длинный."""
        options = sorted((int(h), u) for h, u in (self.cfg.get("catchup_urls") or {}).items())
        if gap_h is not None and gap_h <= 5:
            return self.cfg["live_url"]
        for hours, url in options:
            if gap_h is not None and gap_h <= hours - 1:
                return url
        return options[-1][1] if options else self.cfg["live_url"]

    def covered_days(self, conn, start: date, end: date) -> set[date]:
        return days_with_data(conn, records.c.valid_from, [records.c.source == self.name, records.c.quantity == "p_ge10"],
                              start, end, min_count=200)

    def backfill(self, engine, start: date, end: date) -> IngestResult:
        result = IngestResult(self.name)
        sat = self.cfg.get("archive_satellite", 18)
        d = start
        while d <= end:
            url = self.cfg["archive_url"].format(sat=sat, yyyy=f"{d:%Y}", mm=f"{d:%m}", yyyymmdd=f"{d:%Y%m%d}")
            try:
                self.fetch_and_store(engine, url, result, not_found_ok=True)
            except Exception as e:  # noqa: BLE001 - one unavailable archive day is recorded and skipped
                result.errors.append(str(e))
            d += timedelta(days=1)
        return result

    def parse(self, conn, raw_id: int, content: bytes, url: str, fetched_at: datetime) -> int:
        if url.endswith(".json"):
            rows = self._parse_json(content, raw_id, fetched_at)
        else:
            rows = self._parse_nc(content, raw_id, fetched_at)
        return insert_ignore(conn, records, rows)

    def _parse_json(self, content: bytes, raw_id: int, fetched_at: datetime) -> list[dict]:
        rows = []
        for it in json.loads(content):
            e = ENERGIES.get(it.get("energy"))
            flux = it.get("flux")
            if e is None or flux is None:
                continue
            t0 = parse_iso(it["time_tag"])
            rows.append(dict(
                source=self.name, product="swpc_integral_protons", quantity=quantity(e), unit="pfu",
                value=float(flux), valid_from=t0, valid_to=t0 + STEP, issued_at=t0 + STEP,
                issued_at_policy="estimated", fetched_at=fetched_at, kind="observation", region="geo",
                quality={"satellite": it.get("satellite")}, raw_id=raw_id, locator=f"time_tag:{it['time_tag']}"))
        return rows

    def _parse_nc(self, content: bytes, raw_id: int, fetched_at: datetime) -> list[dict]:
        import netCDF4

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.nc"
            path.write_bytes(content)
            ds = netCDF4.Dataset(path)
            try:
                t = np.asarray(ds["time"][:], dtype=float)
                lo = np.asarray(ds["DiffProtonLowerEnergy"][:], dtype=float)
                hi = np.asarray(ds["DiffProtonUpperEnergy"][:], dtype=float)
                diff = np.ma.filled(ds["AvgDiffProtonFlux"][:].astype(float), np.nan)
                int500 = np.ma.filled(ds["AvgIntProtonFlux"][:].astype(float), np.nan)
                created = str(getattr(ds, "date_created", ""))
                platform = str(getattr(ds, "platform", ""))
            finally:
                ds.close()
        epoch = datetime(2000, 1, 1, 12)
        times = [epoch + timedelta(seconds=float(s)) for s in t]
        rows = []
        for e in ENERGIES.values():
            per_sensor = [integrate_sgps(lo[u], hi[u], diff[:, u, :], int500[:, u], e) for u in range(lo.shape[0])]
            # два датчика смотрят в разные стороны; берём максимум (консервативно)
            stacked = np.vstack(per_sensor)
            with np.errstate(all="ignore"):
                val = np.nanmax(np.where(np.isfinite(stacked), stacked, -np.inf), axis=0)
            for t0, v in zip(times, val):
                if not np.isfinite(v) or v < 0:
                    continue
                t0 = t0.replace(microsecond=0)
                rows.append(dict(
                    source=self.name, product="ncei_sgps_l2_integrated", quantity=quantity(e), unit="pfu",
                    value=float(v), valid_from=t0, valid_to=t0 + STEP, issued_at=t0 + STEP,
                    issued_at_policy="reconstructed", fetched_at=fetched_at, kind="observation", region="geo",
                    quality={"platform": platform, "date_created": created, "aperture": "max",
                             "method": "integration of differential channels"},
                    raw_id=raw_id, locator=f"time:{t0:%Y-%m-%dT%H:%M}"))
        return rows
