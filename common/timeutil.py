"""Время. Внутри системы всё хранится как naive datetime в UTC."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import numpy as np

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def parse_iso(value: str | datetime | None) -> datetime | None:
    """ISO-строка (с Z, смещением или без) -> naive UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = value.strip().replace(" ", "T", 1) if "T" not in value else value.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# Время в текстах для людей (объяснения, оповещения, отчёты, консоль) — московское, без перехода на летнее.
# Машиночитаемые поля (ISO в API, JSON, CSV) остаются в UTC.
MSK_OFFSET = timedelta(hours=3)


def msk(value: str | datetime | None, fmt: str = "%H:%M") -> str:
    """UTC (ISO-строка или naive datetime) -> текст по московскому времени."""
    dt = parse_iso(value)
    return (dt + MSK_OFFSET).strftime(fmt) if dt is not None else "—"


_SWPC_TIME = re.compile(r"(\d{4})\s+([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2})(\d{2})\s*(?:UTC|UT)?")


def parse_swpc_time(text: str | None) -> datetime | None:
    """'2024 May 13 1328 UTC' -> datetime(2024, 5, 13, 13, 28)."""
    if not text:
        return None
    m = _SWPC_TIME.search(text)
    if not m:
        return None
    year, mon, day, hh, mm = m.groups()
    hh_i, mm_i = int(hh), int(mm)
    base = datetime(int(year), MONTHS[mon], int(day))
    # SWPC пишет 2400 для конца суток
    return base + timedelta(hours=hh_i, minutes=mm_i)


def to_np(dt: datetime) -> np.datetime64:
    return np.datetime64(dt, "s")


def from_np(value: np.datetime64) -> datetime:
    return value.astype("datetime64[s]").astype(datetime)


def time_grid(start: datetime, end: datetime, step_s: int) -> np.ndarray:
    n = int((end - start).total_seconds() // step_s) + 1
    return to_np(start) + np.arange(n) * np.timedelta64(step_s, "s")


def floor_to(dt: datetime, minutes: int) -> datetime:
    return dt - timedelta(minutes=dt.minute % minutes, seconds=dt.second, microseconds=dt.microsecond)
