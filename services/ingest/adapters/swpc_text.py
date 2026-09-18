"""Текстовые прогнозы SWPC.

swpc_3day: 3-дневный прогноз (Kp по 3-часовым интервалам, вероятность S1 по суткам).
swpc_rsga: отчёт RSGA (вероятности протонного события и вспышек M/X по суткам).
Живые файлы — services.swpc.noaa.gov/text, архив — NCEI swpc-products/daily_reports.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from common.db import insert_ignore, records
from common.timeutil import MONTHS, parse_swpc_time
from services.ingest.adapters.base import Adapter, IngestResult

_DAY_TOKEN = re.compile(r"([A-Z][a-z]{2})\s+(\d{1,2})")


def _issued(text: str) -> datetime:
    m = re.search(r":Issued:\s*([^\n]+)", text)
    issued = parse_swpc_time(m.group(1)) if m else None
    if issued is None:
        raise ValueError("нет строки :Issued:")
    return issued


def _resolve_date(issued: datetime, mon: str, day: int) -> datetime:
    d = datetime(issued.year, MONTHS[mon], day)
    if d < issued - timedelta(days=60):  # переход через Новый год
        d = datetime(issued.year + 1, MONTHS[mon], day)
    return d


def parse_3day(text: str) -> dict:
    text = text.replace("\r", "")
    issued = _issued(text)
    lines = text.split("\n")
    kp_rows, s1 = [], []

    i_kp = next((i for i, ln in enumerate(lines) if "Kp index breakdown" in ln), None)
    if i_kp is not None:
        j = i_kp + 1
        while j < len(lines) and len(_DAY_TOKEN.findall(lines[j])) < 3:
            j += 1
        days = [_resolve_date(issued, mon, int(d)) for mon, d in _DAY_TOKEN.findall(lines[j])[:3]]
        for ln in lines[j + 1:j + 12]:
            m = re.match(r"^\s*(\d{2})-(\d{2})UT\s+(.*)$", ln)
            if not m:
                continue
            h0 = int(m.group(1))
            vals = re.findall(r"\d+(?:\.\d+)?", re.sub(r"\(G\d\)", " ", m.group(3)))
            for day, v in zip(days, vals[:3]):
                t0 = day + timedelta(hours=h0)
                kp_rows.append((t0, t0 + timedelta(hours=3), float(v)))

    i_s = next((i for i, ln in enumerate(lines) if "Solar Radiation Storm Forecast" in ln), None)
    if i_s is not None:
        j = i_s + 1
        while j < len(lines) and len(_DAY_TOKEN.findall(lines[j])) < 3:
            j += 1
        days = [_resolve_date(issued, mon, int(d)) for mon, d in _DAY_TOKEN.findall(lines[j])[:3]]
        for ln in lines[j + 1:j + 6]:
            m = re.match(r"^\s*S1 or greater\s+(\d+)%\s+(\d+)%\s+(\d+)%", ln)
            if m:
                s1 = [(d, d + timedelta(days=1), float(p)) for d, p in zip(days, m.groups())]
                break
    return {"issued": issued, "kp": kp_rows, "s1": s1}


def parse_rsga(text: str) -> dict:
    text = text.replace("\r", "")
    issued = _issued(text)
    m = re.search(r"Event probabilities\s+(\d{1,2})\s+([A-Z][a-z]{2})", text)
    if not m:
        raise ValueError("нет раздела Event probabilities")
    d0 = _resolve_date(issued, m.group(2), int(m.group(1)))
    days = [d0 + timedelta(days=k) for k in range(3)]
    probs = {}
    for key, label in (("prob_m", r"Class M"), ("prob_x", r"Class X"), ("prob_proton", r"Proton")):
        mm = re.search(label + r"\s+(\d+)/(\d+)/(\d+)", text)
        if mm:
            probs[key] = [(d, d + timedelta(days=1), float(p)) for d, p in zip(days, mm.groups())]
    return {"issued": issued, "probs": probs}


class Swpc3Day(Adapter):
    name = "swpc_3day"
    has_backfill = True

    def live(self, engine) -> IngestResult:
        result = IngestResult(self.name)
        self.fetch_and_store(engine, self.cfg["live_url"], result)
        return result

    def backfill(self, engine, start: date, end: date) -> IngestResult:
        result = IngestResult(self.name)
        d = start
        while d <= end:
            for hhmm in ("0030", "1230"):
                url = self.cfg["archive_url"].format(yyyy=f"{d:%Y}", mm=f"{d:%m}", yyyymmdd=f"{d:%Y%m%d}", hhmm=hhmm)
                try:
                    self.fetch_and_store(engine, url, result, not_found_ok=True)
                except Exception as e:
                    result.errors.append(str(e))
            d += timedelta(days=1)
        return result

    def parse(self, conn, raw_id: int, content: bytes, url: str, fetched_at: datetime) -> int:
        p = parse_3day(content.decode("utf-8", errors="replace"))
        issued = p["issued"]
        rows = []
        for t0, t1, v in p["kp"]:
            rows.append(dict(source=self.name, product="3day_forecast", quantity="kp", unit="", value=v,
                             valid_from=t0, valid_to=t1, issued_at=issued, issued_at_policy="exact",
                             fetched_at=fetched_at, kind="forecast", region="planetary", quality=None,
                             raw_id=raw_id, locator=f"kp:{t0:%Y-%m-%dT%H}"))
        for t0, t1, v in p["s1"]:
            rows.append(dict(source=self.name, product="3day_forecast", quantity="prob_s1", unit="%", value=v,
                             valid_from=t0, valid_to=t1, issued_at=issued, issued_at_policy="exact",
                             fetched_at=fetched_at, kind="forecast", region="geo", quality=None,
                             raw_id=raw_id, locator=f"s1:{t0:%Y-%m-%d}"))
        return insert_ignore(conn, records, rows)


class SwpcRsga(Adapter):
    name = "swpc_rsga"
    has_backfill = True

    def live(self, engine) -> IngestResult:
        result = IngestResult(self.name)
        self.fetch_and_store(engine, self.cfg["live_url"], result)
        return result

    def backfill(self, engine, start: date, end: date) -> IngestResult:
        result = IngestResult(self.name)
        d = start
        while d <= end:
            url = self.cfg["archive_url"].format(yyyy=f"{d:%Y}", mm=f"{d:%m}", yyyymmdd=f"{d:%Y%m%d}")
            try:
                self.fetch_and_store(engine, url, result, not_found_ok=True)
            except Exception as e:
                result.errors.append(str(e))
            d += timedelta(days=1)
        return result

    def parse(self, conn, raw_id: int, content: bytes, url: str, fetched_at: datetime) -> int:
        p = parse_rsga(content.decode("utf-8", errors="replace"))
        rows = []
        for q, items in p["probs"].items():
            for t0, t1, v in items:
                rows.append(dict(source=self.name, product="rsga", quantity=q, unit="%", value=v,
                                 valid_from=t0, valid_to=t1, issued_at=p["issued"], issued_at_policy="exact",
                                 fetched_at=fetched_at, kind="forecast", region="sun" if q != "prob_proton" else "geo",
                                 quality=None, raw_id=raw_id, locator=f"{q}:{t0:%Y-%m-%d}"))
        return insert_ignore(conn, records, rows)
