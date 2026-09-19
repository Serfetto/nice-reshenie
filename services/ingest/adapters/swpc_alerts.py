"""Алерты, предупреждения и сводки SWPC.

Живой поток: products/alerts.json (последние ~дни).
Архив: ftp://ftp.swpc.noaa.gov/pub/alerts/alerts_YYYYMM.html (полные тексты за месяц).
"""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta

from common.db import insert_ignore, messages, source_status
from common.timeutil import parse_swpc_time, utcnow
from services.ingest.adapters.base import Adapter, IngestResult, days_with_data, gap_start, last_time

_FIELDS = {
    "code": r"Space Weather Message Code:\s*(\w+)",
    "serial": r"Serial Number:\s*(\d+)",
    "issue": r"Issue Time:\s*([^\n]+)",
    "begin": r"Begin Time:\s*([^\n]+)",
    "max": r"Maximum Time:\s*([^\n]+)",
    "end": r"End Time:\s*([^\n]+)",
    "valid_from": r"Valid From:\s*([^\n]+)",
    "valid_to": r"(?:Now Valid Until|Valid To|Valid Until):\s*([^\n]+)",
    "extension_of": r"Extension to Serial Number:\s*(\d+)",
    "cancel_of": r"Cancel Serial Number:\s*(\d+)",
    "scale": r"(?:Predicted )?NOAA Scale:\s*([^\n]+)",
    "max_flux": r"Maximum \d+\s*MeV Flux:\s*([\d.]+)",
}
_TITLE = re.compile(
    r"^(ALERT|WARNING|EXTENDED WARNING|WATCH|SUMMARY|CONTINUED ALERT|CONTINUED WARNING|CANCEL[A-Z ]*)\s*:\s*(.+)$",
    re.MULTILINE)


def parse_message(text: str) -> dict | None:
    text = text.replace("\r", "")
    found = {}
    for key, rx in _FIELDS.items():
        m = re.search(rx, text)
        found[key] = m.group(1).strip() if m else None
    if not found["code"] or not found["issue"]:
        return None
    issued = parse_swpc_time(found["issue"])
    if issued is None:
        return None
    t = _TITLE.search(text)
    return {
        "code": found["code"],
        "serial": int(found["serial"]) if found["serial"] else None,
        "issued_at": issued,
        "msg_type": t.group(1).strip() if t else None,
        "title": t.group(2).strip() if t else None,
        "begin_time": parse_swpc_time(found["begin"]),
        "max_time": parse_swpc_time(found["max"]),
        "end_time": parse_swpc_time(found["end"]),
        "valid_from": parse_swpc_time(found["valid_from"]),
        "valid_to": parse_swpc_time(found["valid_to"]),
        "extension_of": int(found["extension_of"]) if found["extension_of"] else None,
        "cancel_of": int(found["cancel_of"]) if found["cancel_of"] else None,
        "scale": found["scale"],
        "max_flux": float(found["max_flux"]) if found["max_flux"] else None,
        "text": text.strip(),
    }


def split_archive_html(content: bytes) -> list[str]:
    t = content.decode("utf-8", errors="replace")
    t = html.unescape(re.sub(r"<[^>]+>", "\n", t)).replace("\r", "")
    parts = re.split(r"(?=Space Weather Message Code:)", t)
    out = []
    for p in parts:
        if not p.startswith("Space Weather Message Code:"):
            continue
        # убираем пустые строки, которые оставляет HTML-разметка
        lines = [ln.strip() for ln in p.split("\n")]
        out.append("\n".join(ln for ln in lines if ln))
    return out


class SwpcAlerts(Adapter):
    name = "swpc_alerts"
    has_backfill = True

    def live(self, engine) -> IngestResult:
        """Живая лента хранит ~месяц сообщений; при первом запуске или простое дольше 20 сут — ещё месячные архивы FTP.
        Сообщения выходят по событию, поэтому пропуск считается от последней успешной проверки ленты."""
        result = IngestResult(self.name)
        now = utcnow()
        last = last_time(engine, source_status.c.last_success, source_status.c.source == self.name)
        self.fetch_and_store(engine, self.cfg["live_url"], result)
        if last is None or now - last > timedelta(days=20):
            self._fill(result, "архива FTP", lambda: self.backfill(engine, gap_start(last, now, 31), now.date()))
        return result

    def covered_days(self, conn, start: date, end: date) -> set[date]:
        """Архив — файл на месяц: месяц загружен, если за него есть сообщения."""
        days = days_with_data(conn, messages.c.issued_at, [messages.c.source == self.name], start, end)
        months = {(d.year, d.month) for d in days}
        out, d = set(), start
        while d <= end:
            if (d.year, d.month) in months:
                out.add(d)
            d += timedelta(days=1)
        return out

    def backfill(self, engine, start: date, end: date) -> IngestResult:
        result = IngestResult(self.name)
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            url = self.cfg["archive_url"].format(yyyymm=f"{y}{m:02d}")
            try:
                self.fetch_and_store(engine, url, result, not_found_ok=True)
            except Exception as e:  # noqa: BLE001 - a missing monthly archive must not halt backfill
                result.errors.append(str(e))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return result

    def parse(self, conn, raw_id: int, content: bytes, url: str, fetched_at: datetime) -> int:
        if url.endswith(".json"):
            items = json.loads(content)
            texts = [it.get("message", "") for it in items]
        else:
            texts = split_archive_html(content)
        rows = []
        for txt in texts:
            msg = parse_message(txt)
            if msg is None:
                continue
            msg.update(source=self.name, fetched_at=fetched_at, raw_id=raw_id,
                       locator=f"serial:{msg['serial']}" if msg["serial"] is not None else None)
            rows.append(msg)
        return insert_ignore(conn, messages, rows)
