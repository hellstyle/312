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


# ─────────────────────────────── bindings ───────────────────────────────────

BIND_TO_NEW_MESSAGE = [
    handle_rental_select_game,
    handle_rental_downloaded,
    handle_offline_done,
    handle_rental_status,
    handle_rental_extend,
]
BIND_TO_LAST_CHAT_MESSAGE_CHANGED = [
    handle_rental_select_game,
    handle_rental_downloaded,
    handle_offline_done,
    handle_rental_status,
    handle_rental_extend,
]
