"""Telegram-бот оповещений.

Подключение без настройки id чата: пользователь открывает бота и нажимает Start — бот отвечает, что оповещения
подключены, и запоминает чат (таблица tg_chats). /status — что отслеживается сейчас, /stop — отключить.
Команды принимаются длинным опросом getUpdates в фоне сервиса assessment. Опрашивать один токен может только
один процесс: второй получает 409 — это видно в статусе (консоль, раздел «Оповещения»).
TELEGRAM_CHAT_ID из .env по-прежнему работает как дополнительный получатель.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import httpx
from sqlalchemy import select, update

from common import config
from common.db import get_engine, insert_ignore, tg_chats, watches
from common.timeutil import iso, msk, utcnow

log = logging.getLogger(__name__)

STATE: dict = {"polling": False, "error": None, "last_poll": None}
_client = httpx.Client()
_bot: dict = {"info": None, "checked": 0.0}
_started = False

MECH_TEXT = {"radiation": "Частицы от Солнца", "mmod": "Мусор и метеороиды"}
LEVEL_TEXT = {"acceptable": "без замечаний", "undesirable": "нежелательно", "no_data": "нет данных",
              "critical": "СТОП-ФАКТОР"}


class TelegramError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def token() -> str | None:
    return config.secret("TELEGRAM_BOT_TOKEN")


def call(method: str, http_timeout: float = 15, **params) -> dict | list | bool:
    try:
        r = _client.post(f"https://api.telegram.org/bot{token()}/{method}", json=params, timeout=http_timeout)
    except httpx.HTTPError as e:
        raise TelegramError(f"нет связи с Telegram: {type(e).__name__}") from e
    try:
        data = r.json()
    except ValueError:
        data = {}
    if not data.get("ok"):
        raise TelegramError(f"HTTP {r.status_code}: {data.get('description') or r.text[:200]}", r.status_code)
    return data["result"]


def bot_info() -> dict | None:
    """getMe с кешем (неудача — повтор не чаще раза в минуту)."""
    if not token():
        return None
    if _bot["info"] is None and time.monotonic() - _bot["checked"] > 60:
        _bot["checked"] = time.monotonic()
        try:
            _bot["info"] = call("getMe")
        except TelegramError as e:
            STATE["error"] = f"бот недоступен: {e}"
    return _bot["info"]


def env_chat() -> tuple[str | None, str | None]:
    """(id чата из .env, проблема). Частая ошибка — id самого бота (число перед «:» в токене): бот не пишет сам себе."""
    chat = (config.secret("TELEGRAM_CHAT_ID") or "").strip()
    if not chat:
        return None, None
    if token() and chat == token().split(":")[0]:
        return None, ("TELEGRAM_CHAT_ID в .env — это id самого бота, а бот не может писать сам себе. "
                      "Он не нужен: откройте бота и нажмите Start — чат подключится сам.")
    return chat, None


# ---------- подписчики ----------

def _title(chat: dict) -> str:
    name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
    return chat.get("title") or name or (f"@{chat['username']}" if chat.get("username") else str(chat["id"]))


def subscribe(chat: dict) -> None:
    now = utcnow()
    with get_engine().begin() as conn:
        insert_ignore(conn, tg_chats, [{"chat_id": chat["id"], "title": _title(chat), "active": True, "subscribed_at": now}])
        conn.execute(update(tg_chats).where(tg_chats.c.chat_id == chat["id"])
                     .values(title=_title(chat), active=True, subscribed_at=now, last_error=None))


def unsubscribe(chat_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(update(tg_chats).where(tg_chats.c.chat_id == chat_id).values(active=False))


def active_chats() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(tg_chats).where(tg_chats.c.active.is_(True)).order_by(tg_chats.c.subscribed_at)).fetchall()
    return [{"chat_id": r.chat_id, "title": r.title, "subscribed_at": iso(r.subscribed_at)} for r in rows]


# ---------- отправка ----------

def send(chat_id, text: str, keyboard: dict | None = None) -> str:
    """'ok' или текст ошибки. Бот заблокирован или чат удалён — чат отписывается."""
    params = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if keyboard is not None:
        params["reply_markup"] = keyboard
    try:
        call("sendMessage", **params)
        return "ok"
    except TelegramError as e:
        if e.status == 403:
            with get_engine().begin() as conn:
                conn.execute(update(tg_chats).where(tg_chats.c.chat_id == int(chat_id))
                             .values(active=False, last_error=str(e)))
        return str(e)


def broadcast(text: str) -> dict[str, str]:
    """Отправить всем подключённым чатам (и чату из .env). {чат: результат}."""
    if not token():
        return {}
    chats = active_chats()
    out = {c["title"] or str(c["chat_id"]): send(c["chat_id"], text) for c in chats}
    env, _problem = env_chat()
    if env and env not in {str(c["chat_id"]) for c in chats}:
        out[f"чат {env} из .env"] = send(env, text)
    return out


def deliver(text: str) -> str:
    """Отправка оповещения; итог одной строкой — для отметки о доставке в консоли."""
    if not token():
        return "не настроен (нет TELEGRAM_BOT_TOKEN)"
    res = broadcast(text)
    if not res:
        return "никто не подключён — откройте бота и нажмите Start"
    ok = [k for k, v in res.items() if v == "ok"]
    if len(ok) == len(res):
        return f"доставлено: {', '.join(ok)}"
    bad = next(v for v in res.values() if v != "ok")
    return f"доставлено {len(ok)} из {len(res)}; ошибка: {bad}"


# ---------- статус для консоли ----------

def status() -> dict:
    if not token():
        return {"configured": False}
    bot = bot_info()
    _env, problem = env_chat()
    username = (bot or {}).get("username")
    return {"configured": True, "bot_username": username, "link": f"https://t.me/{username}" if username else None,
            "chats": active_chats(), "env_chat": bool(_env), "env_problem": problem,
            "polling": STATE["polling"], "polling_enabled": _polling_enabled(), "error": STATE["error"]}


def brief_status() -> dict:
    """Коротко — для ответа на «Следить»: сколько чатов получат сообщения."""
    st = status()
    if not st["configured"]:
        return {"state": "not_configured"}
    n = len(st["chats"]) + (1 if st["env_chat"] else 0)
    return {"state": "ok" if n else "no_chats", "chats": n, "bot_username": st["bot_username"], "link": st["link"]}


# ---------- команды бота ----------

def watches_text() -> str:
    with get_engine().connect() as conn:
        rows = conn.execute(select(watches).where(watches.c.status == "active").order_by(watches.c.created_at)).fetchall()
    if not rows:
        return "Сейчас ни один выход не отслеживается."
    lines = [f"Сейчас отслеживается: {len(rows)}"]
    for w in rows[:10]:
        mode = " (прокрутка прошлого дня)" if w.mode == "replay" else ""
        state = ""
        if w.last_snapshot:
            state = " — " + "; ".join(f"{MECH_TEXT.get(n, n)}: {LEVEL_TEXT.get(m['worst'], m['worst'])}"
                                      for n, m in w.last_snapshot["mechanisms"].items())
        lines.append(f"• {w.label or w.id}: {msk(w.window_start, '%d.%m %H:%M')}–{msk(w.window_end)} МСК{mode}{state}")
    return "\n".join(lines)


# Кнопки под полем ввода (нажатие отправляет текст кнопки) и меню команд у кнопки «Меню»
BTN_STATUS, BTN_PING, BTN_STOP, BTN_START, BTN_HELP = (
    "📋 Что отслеживается", "🔔 Проверить связь", "🔕 Отключить оповещения", "🔔 Подключить оповещения", "❓ Помощь")
BUTTON_CMD = {BTN_STATUS: "/status", BTN_PING: "/ping", BTN_STOP: "/stop", BTN_START: "/start", BTN_HELP: "/help"}
COMMANDS = [("start", "подключить оповещения"), ("status", "что отслеживается сейчас"),
            ("ping", "проверить, что бот работает"), ("stop", "отключить оповещения"), ("help", "что умеет бот")]


def keyboard(subscribed: bool) -> dict:
    rows = ([[BTN_STATUS, BTN_PING], [BTN_STOP, BTN_HELP]] if subscribed else [[BTN_START], [BTN_HELP]])
    return {"keyboard": [[{"text": t} for t in row] for row in rows], "resize_keyboard": True, "is_persistent": True}


HELP = ("Кнопки внизу (или меню команд):\n"
        f"{BTN_STATUS} — /status\n{BTN_PING} — /ping\n{BTN_STOP} — /stop\n\n"
        "Поставить выход на отслеживание — в веб-консоли: кнопка «Следить…» под ответом.")


def welcome_text() -> str:
    return ("✅ Оповещения ВКД подключены.\n\n"
            "Сюда придёт сообщение, когда для отслеживаемого выхода изменится обстановка: появится стоп-фактор, "
            "станет хуже, пропадут данные или станет лучше. Пока ничего не меняется, бот молчит — это нормально.\n\n"
            f"{watches_text()}\n\n{HELP}")


def _subscribed(chat_id: int) -> bool:
    return any(c["chat_id"] == chat_id for c in active_chats())


def handle(upd: dict) -> None:
    msg = upd.get("message") or {}
    chat, text = msg.get("chat"), (msg.get("text") or "").strip()
    if not chat or not text:
        return
    cmd = BUTTON_CMD.get(text) or (text.split()[0].split("@")[0].lower() if text.startswith("/") else "")
    cid = chat["id"]
    if cmd == "/start":
        subscribe(chat)
        send(cid, welcome_text(), keyboard(True))
    elif cmd == "/stop":
        unsubscribe(cid)
        send(cid, f"🔕 Оповещения отключены. Чтобы снова получать их — кнопка «{BTN_START}».", keyboard(False))
    elif cmd == "/status":
        on = _subscribed(cid)
        send(cid, ("🔔 Оповещения в этот чат включены." if on else f"🔕 Оповещения в этот чат выключены — «{BTN_START}».")
             + f"\n\n{watches_text()}", keyboard(on))
    elif cmd == "/ping":
        on = _subscribed(cid)
        send(cid, f"✅ Бот работает, сейчас {msk(utcnow(), '%d.%m %H:%M')} МСК. "
                  + ("Оповещения приходят в этот чат." if on else f"Оповещения в этот чат выключены — «{BTN_START}»."),
             keyboard(on))
    elif cmd == "/help":
        send(cid, "Я присылаю оповещения о выходах в открытый космос.\n\n" + HELP, keyboard(_subscribed(cid)))
    else:
        send(cid, "Не понял команду. Нажмите кнопку внизу или «Меню».\n\n" + HELP, keyboard(_subscribed(cid)))


def set_commands() -> None:
    """Меню команд бота (кнопка «Меню» рядом с полем ввода)."""
    try:
        call("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])
    except TelegramError as e:
        log.warning("Telegram: меню команд не установлено: %s", e)


# ---------- фоновый приём команд ----------

def _polling_enabled() -> bool:
    return bool(token()) and os.getenv("TELEGRAM_POLLING", "1") == "1"


def _poll_loop() -> None:
    offset = None
    set_commands()
    while True:
        try:
            # первый запрос — без ожидания: статус «принимает команды» виден сразу после старта
            params = {"timeout": 50 if STATE["polling"] else 0, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset
            updates = call("getUpdates", http_timeout=70, **params)
            STATE.update(polling=True, error=None, last_poll=iso(utcnow()))
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    handle(u)
                except Exception:
                    log.exception("Telegram: ошибка обработки команды")
        except TelegramError as e:
            if e.status == 409:
                err = ("команды бота принимает другой запущенный экземпляр сервиса (409 Conflict) — остановите лишний, "
                       "иначе нажатие Start может дойти не до этого сервера")
            elif e.status == 401:
                err = "неверный TELEGRAM_BOT_TOKEN (401)"
            else:
                err = str(e)
            STATE.update(polling=False, error=err)
            log.warning("Telegram: %s", err)
            time.sleep(300 if e.status == 401 else 30)
        except Exception as e:
            STATE.update(polling=False, error=f"{type(e).__name__}: {e}")
            log.exception("Telegram: сбой приёма команд")
            time.sleep(15)


def start_background() -> None:
    global _started
    if _started or not _polling_enabled():
        return
    _started = True
    threading.Thread(target=_poll_loop, name="telegram-bot", daemon=True).start()
