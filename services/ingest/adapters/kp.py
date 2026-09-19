"""Индекс Kp.

kp_observed: живой SWPC (оценка в реальном времени) + архив GFZ (окончательные значения,
публикуются позже — в replay это реконструкция).
kp_forecast: прогноз SWPC по 3-часовым интервалам (время выпуска в файле не указано,
берём время получения).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from common.db import insert_ignore, records
from common.timeutil import parse_iso, utcnow
from services.ingest.adapters.base import Adapter, IngestResult, days_with_data, gap_start, last_time

THREE_H = timedelta(hours=3)


def _rows_from_swpc(items) -> list[dict]:
    """SWPC отдаёт либо список словарей, либо таблицу (первая строка — заголовок)."""
    if items and isinstance(items[0], list):
        header = [h.lower() for h in items[0]]
        return [dict(zip(header, r)) for r in items[1:]]
    return [{k.lower(): v for k, v in it.items()} for it in items]


class KpObserved(Adapter):
    name = "kp_observed"
    has_backfill = True

    def live(self, engine) -> IngestResult:
        """Живой ряд SWPC покрывает ~7 сут; пропуск больше (простой) догружается из GFZ."""
        result = IngestResult(self.name)
        now = utcnow()
        last = last_time(engine, records.c.valid_to, records.c.source == self.name)
        self.fetch_and_store(engine, self.cfg["live_url"], result)
        if last is not None and now - last > timedelta(days=6):
            self._fill(result, "GFZ", lambda: self.backfill(engine, gap_start(last, now, 0), now.date()))
        return result

    def covered_days(self, conn, start: date, end: date) -> set[date]:
        return days_with_data(conn, records.c.valid_from, [records.c.source == self.name], start, end, min_count=8)

    def backfill(self, engine, start: date, end: date) -> IngestResult:
        result = IngestResult(self.name)
        url = self.cfg["archive_url"].format(start=f"{start:%Y-%m-%d}T00:00:00Z",
                                             end=f"{end:%Y-%m-%d}T23:59:59Z")
        try:
            self.fetch_and_store(engine, url, result)
        except Exception as e:  # noqa: BLE001 - archive fallback failure is reported in source status
            result.errors.append(str(e))
        return result

    def parse(self, conn, raw_id: int, content: bytes, url: str, fetched_at: datetime) -> int:
        data = json.loads(content)
        rows = []
        if isinstance(data, dict) and "datetime" in data:  # GFZ
            statuses = data.get("status") or [None] * len(data["Kp"])
            for t, kp, st in zip(data["datetime"], data["Kp"], statuses):
                if kp is None:
                    continue
                t0 = parse_iso(t)
                rows.append(dict(
                    source=self.name, product="gfz_kp", quantity="kp", unit="", value=float(kp),
                    valid_from=t0, valid_to=t0 + THREE_H, issued_at=t0 + THREE_H,
                    issued_at_policy="reconstructed", fetched_at=fetched_at, kind="observation",
                    region="planetary", quality={"status": st}, raw_id=raw_id, locator=f"datetime:{t}"))
        else:  # SWPC
            for it in _rows_from_swpc(data):
                kp = it.get("kp")
                if kp is None:
                    continue
                t0 = parse_iso(it["time_tag"])
                rows.append(dict(
                    source=self.name, product="swpc_planetary_k", quantity="kp", unit="", value=float(kp),
                    valid_from=t0, valid_to=t0 + THREE_H, issued_at=t0 + THREE_H,
                    issued_at_policy="estimated", fetched_at=fetched_at, kind="observation",
                    region="planetary", quality={"station_count": it.get("station_count")},
                    raw_id=raw_id, locator=f"time_tag:{it['time_tag']}"))
        return insert_ignore(conn, records, rows)


class KpForecast(Adapter):
    name = "kp_forecast"

    def live(self, engine) -> IngestResult:
        result = IngestResult(self.name)
        self.fetch_and_store(engine, self.cfg["live_url"], result)
        return result

    def parse(self, conn, raw_id: int, content: bytes, url: str, fetched_at: datetime) -> int:
        rows = []
        for it in _rows_from_swpc(json.loads(content)):
            if it.get("observed") != "predicted" or it.get("kp") is None:
                continue
            t0 = parse_iso(it["time_tag"])
            rows.append(dict(
                source=self.name, product="swpc_kp_forecast", quantity="kp", unit="", value=float(it["kp"]),
                valid_from=t0, valid_to=t0 + THREE_H, issued_at=fetched_at, issued_at_policy="fetched",
                fetched_at=fetched_at, kind="forecast", region="planetary",
                quality={"noaa_scale": it.get("noaa_scale")}, raw_id=raw_id, locator=f"time_tag:{it['time_tag']}"))
        return insert_ignore(conn, records, rows)
