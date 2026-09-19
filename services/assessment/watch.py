"""Отслеживание окна и оповещения.

Окно ВКД ставится на отслеживание. Фоновый поток пересчитывает его тем же ядром:
- режим now: по сигналу «новые данные» от ingest (PostgreSQL LISTEN/NOTIFY) и по таймеру;
- режим replay: проигрывание исторического дня — «часы» идут с шагом sim_step_min за такт,
  расчёт на каждом шаге видит только данные, опубликованные до текущего момента «часов».
Оповещение создаётся при изменении состояния окна: ухудшение класса по механизму, появление
критического интервала, пропажа данных, улучшение (информационно). Повторов по тому же
состоянию нет. Доставка: веб-консоль (через report, SSE) и Telegram, если задан бот.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select, update

from common import config
from common.db import alerts, get_engine, watches
from common.timeutil import iso, msk, parse_iso, utcnow
from services.assessment.core.explain import conjunction_text
from services.assessment.core.timeline import REASONS
from services.assessment.runner import RunError, execute, jsonable
from services.assessment.schemas import RunRequest

log = logging.getLogger(__name__)

RANK = {"acceptable": 0, "undesirable": 1, "no_data": 2, "critical": 3}
LEVEL_TEXT = {"acceptable": "приемлемо", "undesirable": "нежелательно", "no_data": "нет данных",
              "critical": "критично"}
MECH_TEXT = {"radiation": "Радиация", "mmod": "Мусор и метеороиды"}
TICK_S = float(os.getenv("WATCH_TICK_S", "5"))
NOW_INTERVAL_S = float(os.getenv("WATCH_NOW_INTERVAL_S", "120"))

_wake = threading.Event()
_started = False


# ---------- создание и управление ----------

def create(body: dict) -> str:
    """body: mode, window_start, duration_min, [as_of — старт «часов» для replay], sim_step_min, eva,
    mechanisms, channels, label."""
    mode = body.get("mode", "now")
    if mode not in ("now", "replay"):
        raise RunError("Режим отслеживания: now или replay")
    start = parse_iso(body["window_start"])
    duration = int(body.get("duration_min", 390))
    end = start + timedelta(minutes=duration)
    req = {"duration_min": duration, "eva": body.get("eva") or {},
           "mechanisms": body.get("mechanisms") or ["radiation", "mmod"],
           "radiation_model": body.get("radiation_model", "team")}
    RunRequest(mode="now", **req)  # проверка параметров
    wid = "w_" + uuid.uuid4().hex[:10]
    row = {"id": wid, "created_at": utcnow(), "mode": mode, "label": body.get("label"), "request": req,
           "window_start": start, "window_end": end, "status": "active",
           "channels": body.get("channels") or ["web"], "n_alerts": 0}
    if mode == "replay":
        sim0 = parse_iso(body.get("as_of")) or (start - timedelta(hours=6))
        row.update(sim_time=sim0, sim_step_min=int(body.get("sim_step_min", 30)),
                   sim_end=parse_iso(body.get("sim_end")) or end)
    with get_engine().begin() as conn:
        conn.execute(watches.insert().values(**row))
    _wake.set()
    return wid


def stop(wid: str) -> bool:
    with get_engine().begin() as conn:
        res = conn.execute(update(watches).where(watches.c.id == wid, watches.c.status == "active")
                           .values(status="stopped"))
    return res.rowcount > 0


def notify_new_data() -> None:
    _wake.set()


# ---------- оценка и сравнение ----------

def _snapshot(w, as_of: datetime | None) -> dict:
    req = RunRequest(mode="replay" if w.mode == "replay" else "now", as_of=as_of,
                     earliest_start=w.window_start, latest_start=w.window_start, planned_start=w.window_start,
                     **w.request)
    res = execute(req)
    win = next(x for x in res["windows"] if x["is_planned"])
    now = parse_iso(res["as_of"])
    mechs = {}
    for name, st in win["mechanisms"].items():
        first_crit = None
        for iv in res["timeline"].get(name, []):
            if iv["class"] == "critical" and iv["to"] > win["start"] and iv["from"] < win["end"]:
                first_crit = {"from": max(iv["from"], win["start"]), "reason": iv["reason_text"],
                              "kind": iv["kind"], "confidence": iv["confidence"], "evidence": iv["evidence"]}
                conj = next((res["evidence"][k] for k in iv["evidence"] if k.startswith("conj:") and k in res["evidence"]),
                            None)
                if conj:
                    first_crit["detail"] = conjunction_text(conj)
                break
        mechs[name] = {"worst": st["worst"], "minutes": st["minutes"], "confidence": st["confidence"],
                       "reasons": st["reasons"], "first_critical": first_crit}
    latest = [parse_iso(m["latest_issued"]) for m in res["manifest"].values() if m.get("latest_issued")]
    latest_issued = max(latest) if latest else None
    return {"as_of": res["as_of"], "status": win["status"], "window": {"start": win["start"], "end": win["end"]},
            "mechanisms": mechs, "overrun": win["overrun"], "confidence": win["confidence"],
            "sources": {s["source"]: s["state"] for s in res["sources"]},
            "latest_issued": iso(latest_issued),
            "in_progress": parse_iso(win["start"]) <= now < parse_iso(win["end"])}


def _compare(w, prev: dict | None, cur: dict) -> list[dict]:
    out = []
    ret_min = (w.request.get("eva") or {}).get("return_to_airlock_min", 30)
    now = parse_iso(cur["as_of"])
    if prev is None:
        parts = [f"{MECH_TEXT.get(n, n)}: {LEVEL_TEXT[m['worst']]}" for n, m in cur["mechanisms"].items()]
        out.append({"severity": "info", "kind": "watch_started", "mechanism": None,
                    "message": f"Отслеживание начато. Окно {msk(cur['window']['start'])}–"
                               f"{msk(cur['window']['end'])} МСК: " + "; ".join(parts) + "."})
        prev = {"mechanisms": {n: {"worst": "acceptable", "first_critical": None} for n in cur["mechanisms"]}}
        # сразу сообщаем о неблагоприятном начальном состоянии
    for name, m in cur["mechanisms"].items():
        p = prev["mechanisms"].get(name, {"worst": "acceptable"})
        a, b = RANK[p["worst"]], RANK[m["worst"]]
        title = MECH_TEXT.get(name, name)
        if b > a:
            if m["worst"] == "critical":
                fc = m["first_critical"] or {}
                t_crit = parse_iso(fc.get("from")) if fc.get("from") else None
                lead = (t_crit - now).total_seconds() / 60 if t_crit else None
                msg = (f"{title}: КРИТИЧНО в окне с {msk(fc.get('from'))} МСК —{fc.get('reason', '')} "
                       f"({fc.get('kind', '')}, уверенность {fc.get('confidence', '')}).")
                if fc.get("detail"):
                    msg += f" Подробно: {fc['detail']}."
                if cur["in_progress"] and lead is not None:
                    msg += (f" Выход идёт: до критического интервала {lead:.0f} мин, время возвращения в шлюз "
                            f"{ret_min} мин" + (" — ЗАПАС МЕНЬШЕ ВРЕМЕНИ ВОЗВРАЩЕНИЯ." if lead < ret_min else "."))
                elif lead is not None:
                    msg += f" До критического интервала {lead:.0f} мин."
                out.append({"severity": "critical", "kind": "critical_in_window", "mechanism": name, "message": msg,
                            "details": {"first_critical": fc, "lead_min": lead, "return_to_airlock_min": ret_min}})
            elif m["worst"] == "no_data":
                out.append({"severity": "warning", "kind": "data_lost", "mechanism": name,
                            "message": f"{title}: нет данных на части окна ({m['minutes']['no_data']:.0f} мин) — "
                                       "оценка невозможна, окно требует проверки.",
                            "details": {"sources": cur["sources"]}})
            else:
                if set(m["reasons"]) <= {"saa", "meteor_shower"}:
                    continue  # ЮАА и метеорные потоки известны заранее (геометрия, годовой прогноз) — не повод для тревоги
                top = next((c for c in m["reasons"] if c not in ("saa", "meteor_shower")), "")
                top = REASONS.get(top, top)
                out.append({"severity": "warning", "kind": "worsened", "mechanism": name,
                            "message": f"{title}: условия ухудшились до «{LEVEL_TEXT[m['worst']]}» "
                                       f"({m['minutes']['undesirable']:.0f} мин; {top}).",
                            "details": {"reasons": m["reasons"]}})
        elif b < a and prev is not None:
            out.append({"severity": "info", "kind": "improved", "mechanism": name,
                        "message": f"{title}: условия улучшились до «{LEVEL_TEXT[m['worst']]}».",
                        "details": {}})
    return out


def _send_telegram(text: str) -> str | None:
    token, chat = config.secret("TELEGRAM_BOT_TOKEN"), config.secret("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return "не настроен"
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       data={"chat_id": chat, "text": text}, timeout=15)
        return "ok" if r.status_code == 200 else f"HTTP {r.status_code}"
    except Exception as e:
        return f"ошибка: {e}"


def _deliver(w, items: list[dict], cur: dict) -> None:
    if not items:
        return
    now = utcnow()
    as_of = parse_iso(cur["as_of"])
    latest = parse_iso(cur["latest_issued"]) if cur.get("latest_issued") else None
    rows = []
    for it in items:
        delivered = {"web": "ok"}
        if "telegram" in (w.channels or []) and it["severity"] != "info":
            prefix = "[ПРОИГРЫВАНИЕ] " if w.mode == "replay" else ""
            delivered["telegram"] = _send_telegram(f"{prefix}ВКД {w.label or w.id} · {msk(as_of, '%d.%m %H:%M')} МСК\n"
                                                   f"{it['message']}")
        rows.append({"watch_id": w.id, "created_at": now, "as_of": as_of, "severity": it["severity"],
                     "kind": it["kind"], "mechanism": it.get("mechanism"), "message": it["message"],
                     "details": jsonable(it.get("details") or {}), "data_latest_issued": latest,
                     "data_lag_s": int((as_of - latest).total_seconds()) if latest else None,
                     "delivered": delivered})
    with get_engine().begin() as conn:
        conn.execute(alerts.insert(), rows)
        conn.execute(update(watches).where(watches.c.id == w.id)
                     .values(n_alerts=(w.n_alerts or 0) + len(rows)))


def _process(w) -> None:
    now = utcnow()
    values: dict = {"last_check_at": now}
    if w.mode == "replay":
        as_of = w.sim_time
        if as_of > w.sim_end:
            values["status"] = "finished"
            with get_engine().begin() as conn:
                conn.execute(update(watches).where(watches.c.id == w.id).values(**values))
            return
        values["sim_time"] = as_of + timedelta(minutes=w.sim_step_min or 30)
    else:
        as_of = None
        if now > w.window_end:
            values["status"] = "expired"
    try:
        cur = _snapshot(w, as_of)
        items = _compare(w, w.last_snapshot, cur)
        _deliver(w, items, cur)
        values["last_snapshot"] = cur
        values["error"] = None
    except Exception as e:
        log.exception("Отслеживание %s: ошибка расчёта", w.id)
        values["error"] = f"{type(e).__name__}: {e}"
    with get_engine().begin() as conn:
        conn.execute(update(watches).where(watches.c.id == w.id).values(**values))


def tick(force_now: bool = False) -> None:
    now = utcnow()
    with get_engine().connect() as conn:
        active = conn.execute(select(watches).where(watches.c.status == "active")).fetchall()
    for w in active:
        if w.mode == "now" and not force_now and w.last_check_at is not None \
                and (now - w.last_check_at).total_seconds() < NOW_INTERVAL_S:
            continue
        _process(w)


# ---------- фоновые потоки ----------

def _loop() -> None:
    while True:
        forced = _wake.wait(timeout=TICK_S)
        _wake.clear()
        try:
            tick(force_now=forced)
        except Exception:
            log.exception("Ошибка такта отслеживания")


def _listen_pg() -> None:
    """Сигнал о новых данных от ingest (только PostgreSQL)."""
    import time

    import psycopg

    dsn = config.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
    while True:
        try:
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute("LISTEN new_data")
                for _n in conn.notifies():
                    _wake.set()
        except Exception as e:
            log.warning("LISTEN new_data прерван: %s — повтор через 10 с", e)
            time.sleep(10)


def start_background() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="watch-loop", daemon=True).start()
    if config.DATABASE_URL.startswith("postgresql"):
        threading.Thread(target=_listen_pg, name="watch-listen", daemon=True).start()
