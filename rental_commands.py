#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Команды для управления сессиями (аренда и оффлайн активация).

Типы сессий:
  rental  — аренда: таймер стартует после !downloaded, предложение продлить,
             !code отзывается по истечении.
  offline — оффлайн активация: 2 ч с момента первого !code, после перехода в
             оффлайн (!offline_done) !code отзывается навсегда.

Инструкции отправляются автоматически при создании сессии (из handlers.py).
"""

from __future__ import annotations
from typing import TYPE_CHECKING
import logging
from threading import Thread

if TYPE_CHECKING:
    from cardinal import Cardinal

from FunPayAPI.updater.events import NewMessageEvent, LastChatMessageChangedEvent
from rental_system import get_rental_manager, RentalSession

logger = logging.getLogger("RentalCommands")

# ─────────────────────────────── helpers ────────────────────────────────────

def _extract(c: "Cardinal", e):
    """Возвращает (chat_id, chat_name, username, mtext) или None если своё сообщение."""
    if not c.autoresponse_enabled:
        return None
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return None
        if e.message.author == c.account.username:
            return None
        return e.message.chat_id, e.message.chat_name, e.message.author, str(e.message)
    else:
        return e.chat.id, e.chat.name, e.chat.name, str(e.chat)


def _send(c, chat_id, text, chat_name):
    Thread(target=c.send_message, args=(chat_id, text, chat_name),
           kwargs={"watermark": False}, daemon=True).start()


def _fmt_time(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if h and m:
        return f"{h}ч {m}мин"
    if h:
        return f"{h}ч"
    return f"{m}мин"


def _active_session(chat_id) -> RentalSession | None:
    rm = get_rental_manager()
    if not rm:
        return None
    return rm.get_active_session_for_chat(chat_id)


# ─────────────────────────── !select_game ───────────────────────────────────

def handle_rental_select_game(c: "Cardinal", e):
    r = _extract(c, e)
    if not r:
        return
    chat_id, chat_name, username, mtext = r

    if not mtext.lower().startswith("!select_game"):
        return

    parts = mtext.split(maxsplit=1)
    if len(parts) < 2:
        _send(c, chat_id,
              "❌ Использование: !select_game [название_игры]\nПример: !select_game Rust",
              chat_name)
        return

    game_name = parts[1].strip()
    session = _active_session(chat_id)
    if not session:
        _send(c, chat_id, "❌ Нет активной сессии. Сначала оформите покупку.", chat_name)
        return
    if session.is_offline:
        _send(c, chat_id,
              "ℹ️ При оффлайн активации выбор игры не требуется — войдите в аккаунт и "
              "запустите нужную игру из библиотеки Steam.", chat_name)
        return

    get_rental_manager().mark_game_selected(session.session_id)
    _send(c, chat_id,
          f"✅ Игра <b>{game_name}</b> выбрана!\n\n"
          f"⬇️ Скачайте игру и напишите <code>!downloaded</code> — тогда начнётся отсчёт времени.",
          chat_name)


# ──────────────────────────── !downloaded ───────────────────────────────────

def handle_rental_downloaded(c: "Cardinal", e):
    r = _extract(c, e)
    if not r:
        return
    chat_id, chat_name, username, mtext = r

    if mtext.strip().lower() != "!downloaded":
        return

    session = _active_session(chat_id)
    if not session:
        _send(c, chat_id, "❌ Нет активной сессии.", chat_name)
        return
    if session.is_offline:
        _send(c, chat_id,
              "ℹ️ При оффлайн активации команда !downloaded не нужна.\n"
              "После запуска игры в режиме оффлайн напишите <code>!offline_done</code>.",
              chat_name)
        return
    if session.game_downloaded:
        _send(c, chat_id, "ℹ️ Игра уже отмечена как скачанная, таймер идёт.", chat_name)
        return

    get_rental_manager().mark_game_downloaded(session.session_id)
    remaining = _fmt_time(session.get_remaining_time())
    _send(c, chat_id,
          f"✅ Отлично! Таймер аренды запущен.\n\n"
          f"🎮 Игра: <b>{session.game_name}</b>\n"
          f"⏳ Осталось: <b>{remaining}</b>\n\n"
          f"Статус: <code>!rental_status</code> | Продлить: <code>!extend N</code>",
          chat_name)


# ──────────────────────────── !offline_done ─────────────────────────────────

def handle_offline_done(c: "Cardinal", e):
    """
    Покупатель подтвердил что игра запущена в оффлайн-режиме.
    После этого !code отзывается — больше не нужен.
    """
    r = _extract(c, e)
    if not r:
        return
    chat_id, chat_name, username, mtext = r

    if mtext.strip().lower() != "!offline_done":
        return

    session = _active_session(chat_id)
    if not session:
        _send(c, chat_id, "❌ Нет активной сессии.", chat_name)
        return
    if not session.is_offline:
        _send(c, chat_id,
              "ℹ️ Эта команда только для оффлайн активации.", chat_name)
        return
    if session.offline_confirmed:
        _send(c, chat_id,
              "ℹ️ Вы уже подтвердили переход в оффлайн.", chat_name)
        return

    rm = get_rental_manager()
    with rm.lock:
        session.offline_confirmed = True
        session.revoke_code()
        session.active = False
    rm.save_sessions()

    _send(c, chat_id,
          "✅ Отлично! Вы перешли в оффлайн-режим.\n\n"
          "🔒 Доступ к коду Steam Guard закрыт — игра теперь работает автономно.\n"
          "Приятной игры! 🎮",
          chat_name)
    logger.info(f"Оффлайн сессия {session.session_id} подтверждена, !code отозван")


# ─────────────────────────── !rental_status ─────────────────────────────────

def handle_rental_status(c: "Cardinal", e):
    r = _extract(c, e)
    if not r:
        return
    chat_id, chat_name, username, mtext = r

    if mtext.strip().lower() != "!rental_status":
        return

    rm = get_rental_manager()
    if not rm:
        _send(c, chat_id, "❌ Система сессий не настроена.", chat_name)
        return

    sessions = rm.get_user_sessions(str(chat_id))
    active = [s for s in sessions if s.active]

    if not active:
        _send(c, chat_id, "📊 Нет активных сессий.", chat_name)
        return

    lines = ["📊 <b>Ваши активные сессии:</b>\n"]
    for s in active:
        rem = _fmt_time(s.get_remaining_time())
        stype = "Аренда" if s.is_rental else "Оффлайн активация"
        if s.is_rental:
            if not s.game_downloaded:
                if getattr(s, 'rental_code_used_at', None):
                    dl_rem_secs = max(0, int((s.rental_code_used_at + getattr(s, 'download_timeout_secs', 5400)) - __import__('time').time()))
                    status = f"⬇️ Скачивание, осталось {_fmt_time(dl_rem_secs)} на загрузку"
                else:
                    status = "⏳ Войдите в Steam (!code) чтобы запустить таймер"
            else:
                status = f"▶️ Активна, осталось {rem}"
        else:
            if s.offline_code_used_at is None:
                status = "⏳ Ожидание входа (используйте !code)"
            else:
                status = f"▶️ Таймер идёт, осталось {rem}"
        lines.append(f"🎮 <b>{s.game_name}</b> [{stype}]\n{status}")

    _send(c, chat_id, "\n".join(lines), chat_name)


# ──────────────────────────── !extend ───────────────────────────────────────

def handle_rental_extend(c: "Cardinal", e):
    r = _extract(c, e)
    if not r:
        return
    chat_id, chat_name, username, mtext = r

    if not mtext.lower().startswith("!extend"):
        return

    parts = mtext.split()
    if len(parts) < 2:
        _send(c, chat_id, "❌ Использование: !extend [часы]\nПример: !extend 2", chat_name)
        return
    try:
        hours = int(parts[1])
        if hours < 1:
            raise ValueError
    except ValueError:
        _send(c, chat_id, "❌ Укажите целое число часов (минимум 1).", chat_name)
        return

    session = _active_session(chat_id)
    if not session:
        _send(c, chat_id, "❌ Нет активной аренды для продления.", chat_name)
        return
    if session.is_offline:
        _send(c, chat_id, "ℹ️ Оффлайн активация не продлевается.", chat_name)
        return
    if not session.game_downloaded:
        _send(c, chat_id, "❌ Таймер ещё не запущен — сначала скачайте игру (!downloaded).", chat_name)
        return

    session.end_time += hours * 3600
    session.duration_hours += hours
    get_rental_manager().save_sessions()

    rem = _fmt_time(session.get_remaining_time())
    _send(c, chat_id,
          f"✅ Аренда продлена на {hours} ч!\n"
          f"🎮 {session.game_name} — осталось: <b>{rem}</b>",
          chat_name)


# ──────────────────────────── !Nч / !Nчасов ─────────────────────────────────

def handle_rental_set_hours(c: "Cardinal", e):
    """
    Команда !Nч или !Nчасов / !Nчасов — задаёт длительность текущей сессии.
    Примеры: !12ч, !12часов, !24ч, !6часов
    Только владелец (сообщение себе) или от имени бота в чате покупателя.
    """
    r = _extract(c, e)
    if not r:
        return
    chat_id, chat_name, username, mtext = r

    import re
    m = re.match(r'^!(\d+)\s*(?:ч|час|часов|hours?)\s*$', mtext.strip().lower())
    if not m:
        return

    hours = int(m.group(1))
    if hours < 1 or hours > 720:
        _send(c, chat_id, "❌ Допустимо от 1 до 720 часов.", chat_name)
        return

    session = _active_session(chat_id)
    if not session:
        _send(c, chat_id, "❌ Нет активной сессии.", chat_name)
        return
    if session.is_offline:
        _send(c, chat_id, "ℹ️ Оффлайн активация — длительность задаётся при покупке.", chat_name)
        return

    import time as _time
    rm = get_rental_manager()
    with rm.lock:
        session.duration_hours = hours
        session.end_time = session.start_time + hours * 3600
    rm.save_sessions()

    remaining = _fmt_time(session.get_remaining_time())
    _send(c, chat_id,
          f"⏱ Время аренды установлено: <b>{hours} ч</b>\n"
          f"🎮 {session.game_name} — осталось: <b>{remaining}</b>\n\n"
          f"⚠️ Продления не предусмотрено. По истечении нужно оформить новую покупку.",
          chat_name)
    logger.info(f"Сессия {session.session_id}: длительность установлена {hours} ч")


# ──────────────────────────── !sessions ─────────────────────────────────────

def handle_sessions_list(c: "Cardinal", e):
    """
    Команда !sessions — показывает все активные сессии (для владельца бота).
    Может быть вызвана только если сообщение пришло от самого аккаунта FunPay
    (т.е. отправлено через тест-чат или Telegram → FunPay ответ).
    Для покупателей — !rental_status.
    """
    r = _extract(c, e)
    if not r:
        return
    chat_id, chat_name, username, mtext = r

    if mtext.strip().lower() != "!sessions":
        return

    # Только для владельца (сам аккаунт FunPay)
    if not c.old_mode_enabled and username != c.account.username:
        return

    rm = get_rental_manager()
    if not rm:
        _send(c, chat_id, "❌ Система сессий не настроена.", chat_name)
        return

    active = [s for s in rm.sessions.values() if s.active]
    if not active:
        _send(c, chat_id, "📊 Активных сессий нет.", chat_name)
        return

    import time as _time
    lines = [f"📊 <b>Активные сессии ({len(active)}):</b>\n"]
    for s in active:
        rem = _fmt_time(s.get_remaining_time())
        lines.append(
            f"🆔 <code>{s.session_id[:8]}</code> — {s.game_name}\n"
            f"   👤 User: {s.user_id} | ⏳ {rem} | 🎮 {s.steam_login or '—'}"
        )
    _send(c, chat_id, "\n".join(lines), chat_name)


# ──────────────────────────── !kick ─────────────────────────────────────────

def handle_kick_user(c: "Cardinal", e):
    """
    Команда !kick <session_id_prefix> — принудительно завершает сессию и кикает.
    Только для владельца (сам аккаунт FunPay).
    Пример: !kick a3f2c1d4
    """
    r = _extract(c, e)
    if not r:
        return
    chat_id, chat_name, username, mtext = r

    import re
    m = re.match(r'^!kick\s+(\S+)\s*$', mtext.strip().lower())
    if not m:
        return

    # Только для владельца
    if not c.old_mode_enabled and username != c.account.username:
        return

    prefix = m.group(1)
    rm = get_rental_manager()
    if not rm:
        return

    # Найдём сессию по префиксу session_id
    matches = [s for s in rm.sessions.values() if s.session_id.startswith(prefix) and s.active]
    if not matches:
        _send(c, chat_id, f"❌ Сессия с префиксом <code>{prefix}</code> не найдена.", chat_name)
        return
    if len(matches) > 1:
        ids = ", ".join(s.session_id[:8] for s in matches)
        _send(c, chat_id, f"❌ Неоднозначно — совпало несколько: {ids}. Уточните префикс.", chat_name)
        return

    session = matches[0]
    import configparser as _cp
    cfg = _cp.ConfigParser()
    cfg.read("configs/shared_secrets.cfg", encoding="utf-8")
    shared = cfg[session.steam_login].get("shared_secret", "") if session.steam_login in cfg.sections() else ""
    password = cfg[session.steam_login].get("password", "") if session.steam_login in cfg.sections() else ""
    cred = rm.get_credential(session.session_id)
    if cred:
        password = cred

    from steam_kick import kick_account
    result = kick_account(c, session.steam_login or "", password, shared,
                          session.session_id, method="revoke_sessions", reason="manual_kick",
                          steamid=getattr(session, "steamid", None))
    session.revoke_code()
    rm.end_session(session.session_id)
    rm.save_sessions()

    ok = "✅" if result.get("ok") else "⚠️"
    _send(c, chat_id,
          f"{ok} Сессия <code>{session.session_id[:8]}</code> завершена.\n"
          f"👤 Аккаунт: {session.steam_login} | Результат кика: {result.get('action', '?')}",
          chat_name)


# ─────────────────────────────── bindings ───────────────────────────────────

BIND_TO_NEW_MESSAGE = [
    handle_rental_select_game,
    handle_rental_downloaded,
    handle_offline_done,
    handle_rental_status,
    handle_rental_extend,
    handle_rental_set_hours,
    handle_sessions_list,
    handle_kick_user,
]
BIND_TO_LAST_CHAT_MESSAGE_CHANGED = [
    handle_rental_select_game,
    handle_rental_downloaded,
    handle_offline_done,
    handle_rental_status,
    handle_rental_extend,
    handle_rental_set_hours,
    handle_sessions_list,
    handle_kick_user,
]
