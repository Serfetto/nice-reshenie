"""Telegram-бот: подключение по Start, рассылка, понятные сообщения о слежении (без сети — call подменён)."""
from datetime import datetime

import pytest
from sqlalchemy import select

from common import config, db
from common.timeutil import utcnow
from services.assessment import telegram, watch

BOT_ID = "7000000001"


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    db.get_engine.cache_clear()
    db.init_db()
    sent = []

    def fake_call(method, http_timeout=15, **params):
        if method == "sendMessage":
            if params["chat_id"] == 666:
                raise telegram.TelegramError("HTTP 403: Forbidden: bot was blocked by the user", 403)
            sent.append((params["chat_id"], params["text"]))
            return {}
        if method == "getMe":
            return {"id": int(BOT_ID), "username": "test_vkd_bot"}
        raise AssertionError(method)

    env = {"TELEGRAM_BOT_TOKEN": f"{BOT_ID}:secret", "TELEGRAM_CHAT_ID": BOT_ID}  # частая ошибка: id самого бота
    monkeypatch.setattr(config, "secret", lambda name: env.get(name))
    monkeypatch.setattr(telegram, "call", fake_call)
    monkeypatch.setattr(telegram, "_bot", {"info": None, "checked": 0.0})
    yield sent
    db.get_engine.cache_clear()


def _msg(chat_id, text, first_name="Анна"):
    return {"update_id": 1, "message": {"chat": {"id": chat_id, "type": "private", "first_name": first_name}, "text": text}}


def test_start_subscribes_and_confirms(bot):
    assert telegram.deliver("x") == "никто не подключён — откройте бота и нажмите Start"  # id бота из .env не считается
    st = telegram.status()
    assert st["link"] == "https://t.me/test_vkd_bot" and "id самого бота" in st["env_problem"]

    telegram.handle(_msg(42, "/start"))
    chat, text = bot[-1]
    assert chat == 42 and text.startswith("✅ Оповещения ВКД подключены") and "ни один выход не отслеживается" in text
    assert [c["title"] for c in telegram.active_chats()] == ["Анна"]
    assert telegram.brief_status() == {"state": "ok", "chats": 1, "bot_username": "test_vkd_bot",
                                       "link": "https://t.me/test_vkd_bot"}
    assert telegram.deliver("тест") == "доставлено: Анна"

    telegram.handle(_msg(42, "/stop@test_vkd_bot"))
    assert "Оповещения отключены" in bot[-1][1] and telegram.active_chats() == []
    telegram.handle(_msg(42, "привет"))
    assert "/start" in bot[-1][1]


def test_blocked_chat_unsubscribed(bot):
    telegram.subscribe({"id": 666, "first_name": "Б"})
    telegram.subscribe({"id": 42, "first_name": "Анна"})
    assert telegram.deliver("тест").startswith("доставлено 1 из 2; ошибка: HTTP 403")
    assert [c["chat_id"] for c in telegram.active_chats()] == [42]


def test_watch_messages_go_to_telegram(bot):
    telegram.subscribe({"id": 42, "first_name": "Анна"})
    with db.get_engine().begin() as conn:
        conn.execute(db.watches.insert().values(
            id="w_1", created_at=utcnow(), mode="replay", label="выход 08.06 11:45", request={},
            window_start=datetime(2024, 6, 8, 8, 45), window_end=datetime(2024, 6, 8, 15, 15), status="active",
            channels=["web", "telegram"], n_alerts=0, sim_time=datetime(2024, 6, 8, 3, 0), sim_step_min=30,
            sim_end=datetime(2024, 6, 8, 15, 15)))
    cur = {"as_of": "2024-06-08T03:00:00Z", "window": {"start": "2024-06-08T08:45:00Z", "end": "2024-06-08T15:15:00Z"},
           "mechanisms": {"radiation": {"worst": "undesirable", "reasons": {"sep_shielded": 390}, "minutes": {"undesirable": 390}},
                          "mmod": {"worst": "acceptable", "reasons": {}, "minutes": {}}}, "in_progress": False}
    with db.get_engine().connect() as conn:
        w = conn.execute(select(db.watches)).first()
    items = watch._compare(w, None, cur)
    watch._deliver(w, items, datetime(2024, 6, 8, 3, 0))
    texts = [t for _, t in bot]
    # «слежение включено» тоже уходит в Telegram — пользователь сразу видит, что бот работает
    assert texts[0].startswith("🔔 Слежение включено\nвыход 08.06 11:45: выход 08.06 11:45–18:15 МСК · прокрутка")
    assert "Частицы от Солнца — нежелательно" in texts[0]
    assert texts[1].startswith("🟧 Стало хуже") and "идёт протонное событие" in texts[1]
    with db.get_engine().connect() as conn:
        rows = conn.execute(select(db.alerts.c.kind, db.alerts.c.delivered)).fetchall()
        assert conn.execute(select(db.watches.c.n_alerts)).scalar() == 2
    assert [r.kind for r in rows] == ["watch_started", "worsened"]
    assert rows[0].delivered == {"web": "ok", "telegram": "доставлено: Анна"}

    assert watch.stop("w_1")
    for _ in range(50):  # сообщение о завершении уходит в фоне
        if len(bot) == 3:
            break
        import time
        time.sleep(0.05)
    assert bot[-1][1].startswith("🏁 Слежение завершено") and "остановлено вручную" in bot[-1][1]
