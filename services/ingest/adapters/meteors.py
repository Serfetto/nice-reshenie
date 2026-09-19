"""Прогноз метеорных потоков для низкой орбиты (NASA Meteoroid Environment Office, NTRS).

Выпускается раз в год заранее (2024 г. — 2023-11-02), поэтому пригоден для строгого replay: время
публикации — distributionDate записи NTRS. Из выпуска берём:
- flux_data.txt — почасовой поток метеоров потоков (м⁻²·ч⁻¹) и коэффициент к спорадическому фону
  для четырёх предельных кинетических энергий (6.7 Дж, 105 Дж, 2.83 кДж, 105 кДж);
- таблица 2 документа — радианты (RA, Dec) и скорости крупных потоков, нужные для экранирования Землёй.
"""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta

from common.db import insert_ignore, records
from common.timeutil import parse_iso, utcnow
from services.ingest import http
from services.ingest.adapters.base import Adapter, IngestResult, days_with_data

HOUR = timedelta(hours=1)
_RADIANT = re.compile(r"^([A-Za-z][A-Za-z .]+?)\s+(\d+(?:\.\d+)?)\s+([+\-−]?\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+"
                      r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*$", re.MULTILINE)


def energy_label(joules: float) -> str:
    """6.70e+00 -> '6.7J', 1.05e+05 -> '105000J'."""
    return f"{joules:g}J" if joules < 1000 else f"{joules:.0f}J"


def parse_flux(text: str) -> tuple[list[str], list[tuple[datetime, list[float], list[float]]]]:
    """-> (метки энергий, [(время, потоки ×4, коэффициенты ×4)])."""
    lines = text.replace("\r", "").split("\n")
    labels = []
    for ln in lines:
        if ln.startswith("#") and ln.count(" J") >= 8:
            nums = re.findall(r"(\d\.\d+e[+-]\d+)\s*J", ln)
            labels = [energy_label(float(x)) for x in nums[:4]]
    if len(labels) != 4:
        raise ValueError("не найдены предельные энергии в заголовке flux_data.txt")
    rows = []
    for ln in lines:
        if not ln.strip() or ln.startswith("#"):
            continue
        p = ln.split()
        t = datetime.fromisoformat(f"{p[0]}T{p[1]}")
        vals = [float(x) for x in p[5:13]]  # p[4] — ZHR
        rows.append((t, vals[:4], vals[4:8]))
    return labels, rows


def parse_radiants(text: str) -> list[dict]:
    """Таблица 2 документа: поток, RA, Dec, скорость на высоте 100 км, время максимума (UT)."""
    plain = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    out = []
    for name, ra, dec, v, t in _RADIANT.findall(plain):
        out.append({"name": name.strip(), "ra": float(ra), "dec": float(dec.replace("−", "-")), "speed_km_s": float(v),
                    "peak": datetime.fromisoformat(t.replace(" ", "T"))})
    return out


class MeteorForecast(Adapter):
    name = "meteor_forecast"
    has_backfill = True

    def _years(self) -> dict[int, str]:
        return {int(y): str(i) for y, i in (self.cfg.get("forecasts") or {}).items()}

    def live(self, engine) -> IngestResult:
        """Раз в сутки: выпуски на текущий и следующий год, если они есть в конфиге."""
        y = utcnow().year
        return self._load(engine, [yy for yy in (y, y + 1) if yy in self._years()])

    def backfill(self, engine, start: date, end: date) -> IngestResult:
        return self._load(engine, [y for y in range(start.year, end.year + 1) if y in self._years()])

    def covered_days(self, conn, start: date, end: date) -> set[date]:
        return days_with_data(conn, records.c.valid_from, [records.c.source == self.name,
                                                           records.c.quantity.like("meteor_factor_%")],
                              start, end, min_count=24)

    def _load(self, engine, years: list[int]) -> IngestResult:
        result = IngestResult(self.name)
        for y in years:
            nid = self._years()[y]
            meta = http.get(self.cfg["meta_url"].format(id=nid))
            info = json.loads(meta.content)
            self._issued = parse_iso(info.get("distributionDate") or info.get("created"))
            self._title = info.get("title")
            self.store_response(engine, meta.url, meta, result)
            for key in ("flux_url", "doc_url"):
                self.fetch_and_store(engine, self.cfg[key].format(id=nid, year=y), result)
        return result

    def parse(self, conn, raw_id: int, content: bytes, url: str, fetched_at: datetime) -> int:
        issued = getattr(self, "_issued", None)
        text = content.decode("utf-8", errors="replace")
        base = dict(source=self.name, issued_at=issued, issued_at_policy="exact", fetched_at=fetched_at,
                    kind="forecast", region="leo", raw_id=raw_id)
        rows = []
        if url.endswith("flux_data.txt"):
            if issued is None:
                raise ValueError("не известно время выпуска прогноза")
            labels, data = parse_flux(text)
            for t, flux, factor in data:
                for lab, f, k in zip(labels, flux, factor):
                    rows.append({**base, "product": "nasa_meo_flux", "quantity": f"meteor_factor_{lab}", "unit": "",
                                 "value": k, "valid_from": t, "valid_to": t + HOUR, "quality": {"flux_m2_h": f},
                                 "locator": f"{t:%Y-%m-%dT%H}"})
        elif url.endswith(".pdf.txt"):
            if issued is None:
                raise ValueError("не известно время выпуска прогноза")
            for r in parse_radiants(text):
                rows.append({**base, "product": "nasa_meo_radiants", "quantity": "meteor_radiant", "unit": "km/s",
                             "value": r["speed_km_s"], "valid_from": r["peak"], "valid_to": r["peak"] + timedelta(minutes=1),
                             "quality": {"name": r["name"], "ra_deg": r["ra"], "dec_deg": r["dec"]},
                             "locator": f"shower:{r['name']}"})
        return insert_ignore(conn, records, rows)
