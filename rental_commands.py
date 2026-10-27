#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Команды для управления арендой игр.
"""

from __future__ import annotations
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from cardinal import Cardinal

from FunPayAPI.updater.events import NewMessageEvent, LastChatMessageChangedEvent
from rental_system import get_rental_manager
from threading import Thread

logger = logging.getLogger("RentalCommands")


def handle_rental_select_game(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent):
    """Обработчик команды !select_game - выбор игры для аренды."""
    if not c.autoresponse_enabled:
        return

    # Определяем режим работы
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return
        obj, mtext = e.message, str(e.message)
        chat_id, chat_name, username = e.message.chat_id, e.message.chat_name, e.message.author
    else:
        obj, mtext = e.chat, str(e.chat)
        chat_id, chat_name, username = obj.id, obj.name, obj.name

    # Пропускаем собственные сообщения
    if not c.old_mode_enabled and e.message.author == c.account.username:
        return

    # Реагируем на команду !select_game [название_игры]
    if not mtext.lower().startswith("!select_game"):
        return

    parts = mtext.split(maxsplit=1)
    if len(parts) < 2:
        response_text = "❌ Использование: !select_game [название_игры]\nПример: !select_game Cyberpunk 2077"
        Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()
        return

    game_name = parts[1].strip()
    logger.info(f"Команда !select_game от {username}: {game_name}")

    # Получаем активную сессию пользователя
    rental_manager = get_rental_manager()
    if not rental_manager:
        response_text = "❌ Система аренды не настроена. Обратитесь к администратору."
        Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()
        return

    user_sessions = rental_manager.get_user_sessions(chat_id)
    active_session = None
    
    for session in user_sessions:
        if session.active and not session.game_selected:
            active_session = session
            break

    if not active_session:
        response_text = "❌ Нет активной сессии аренды для выбора игры. Сначала оформите аренду."
        Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()
        return

    # Отмечаем игру как выбранную
    success = rental_manager.mark_game_selected(active_session.session_id)
    
    if success:
        response_text = (
            f"✅ Игра {game_name} выбрана для аренды!\n\n"
            f"📋 Детали аренды:\n"
            f"⏱ Время аренды: {active_session.duration_hours} часов\n"
            f"🎮 Игра: {game_name}\n"
            f"👤 Аккаунт: {active_session.account_name}\n\n"
            f"📥 После скачивания игры отправьте команду: !downloaded\n"
            f"⏰ С этого момента начнется отсчет времени аренды."
        )
    else:
        response_text = "❌ Ошибка при выборе игры. Попробуйте снова."

    Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()


def handle_rental_downloaded(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent):
    """Обработчик команды !downloaded - игра скачана, начинается отсчет времени."""
    if not c.autoresponse_enabled:
        return

    # Определяем режим работы
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return
        obj, mtext = e.message, str(e.message)
        chat_id, chat_name, username = e.message.chat_id, e.message.chat_name, e.message.author
    else:
        obj, mtext = e.chat, str(e.chat)
        chat_id, chat_name, username = obj.id, obj.name, obj.name

    # Пропускаем собственные сообщения
    if not c.old_mode_enabled and e.message.author == c.account.username:
        return

    # Реагируем только на команду !downloaded
    if mtext.strip().lower() != "!downloaded":
        return

    logger.info(f"Команда !downloaded от {username}")

    # Получаем активную сессию пользователя
    rental_manager = get_rental_manager()
    if not rental_manager:
        response_text = "❌ Система аренды не настроена. Обратитесь к администратору."
        Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()
        return

    user_sessions = rental_manager.get_user_sessions(chat_id)
    active_session = None
    
    for session in user_sessions:
        if session.active and session.game_selected and not session.game_downloaded:
            active_session = session
            break

    if not active_session:
        response_text = "❌ Нет активной сессии с выбранной игрой. Сначала выберите игру командой !select_game"
        Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()
        return

    # Отмечаем игру как скачанную
    success = rental_manager.mark_game_downloaded(active_session.session_id)
    
    if success:
        remaining_hours = active_session.get_remaining_hours()
        response_text = (
            f"✅ Игра {active_session.game_name} скачана!\n\n"
            f"⏰ Отсчет времени аренды начался!\n"
            f"📊 Время аренды: {active_session.duration_hours} часов\n"
            f"⏳ Осталось времени: {remaining_hours:.1f} часов\n\n"
            f"🎮 Наслаждайтесь игрой! После окончания времени сессия будет автоматически завершена."
        )
    else:
        response_text = "❌ Ошибка при отметке скачивания. Попробуйте снова."

    Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()


def handle_rental_status(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent):
    """Обработчик команды !rental_status - статус аренды."""
    if not c.autoresponse_enabled:
        return

    # Определяем режим работы
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return
        obj, mtext = e.message, str(e.message)
        chat_id, chat_name, username = e.message.chat_id, e.message.chat_name, e.message.author
    else:
        obj, mtext = e.chat, str(e.chat)
        chat_id, chat_name, username = obj.id, obj.name, obj.name

    # Пропускаем собственные сообщения
    if not c.old_mode_enabled and e.message.author == c.account.username:
        return

    # Реагируем только на команду !rental_status
    if mtext.strip().lower() != "!rental_status":
        return

    logger.info(f"Команда !rental_status от {username}")

    # Получаем сессии пользователя
    rental_manager = get_rental_manager()
    if not rental_manager:
        response_text = "❌ Система аренды не настроена."
        Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()
        return

    user_sessions = rental_manager.get_user_sessions(chat_id)
    
    if not user_sessions:
        response_text = "📊 У вас нет активных аренд."
    else:
        response_text = "📊 Статус ваших аренд:\n\n"
        
        for session in user_sessions:
            if session.active:
                status = "✅ Активна"
                remaining = session.get_remaining_hours()
                if session.game_downloaded:
                    status += f" (осталось {remaining:.1f} ч)"
                else:
                    status += " (ожидание скачивания)"
            else:
                status = "❌ Завершена"
            
            response_text += (
                f"🎮 {session.game_name}\n"
                f"👤 Аккаунт: {session.account_name}\n"
                f"⏱ Длительность: {session.duration_hours} ч\n"
                f"📊 Статус: {status}\n\n"
            )

    Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()


def handle_rental_extend(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent):
    """Обработчик команды !extend [часы] - продление аренды."""
    if not c.autoresponse_enabled:
        return

    # Определяем режим работы
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return
        obj, mtext = e.message, str(e.message)
        chat_id, chat_name, username = e.message.chat_id, e.message.chat_name, e.message.author
    else:
        obj, mtext = e.chat, str(e.chat)
        chat_id, chat_name, username = obj.id, obj.name, obj.name

    # Пропускаем собственные сообщения
    if not c.old_mode_enabled and e.message.author == c.account.username:
        return

    # Реагируем на команду !extend [часы]
    if not mtext.lower().startswith("!extend"):
        return

    parts = mtext.split()
    if len(parts) < 2:
        response_text = "❌ Использование: !extend [количество_часов]\nПример: !extend 2"
        Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()
        return

    try:
        additional_hours = int(parts[1])
    except ValueError:
        response_text = "❌ Неверное количество часов. Используйте число."
        Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()
        return

    logger.info(f"Команда !extend от {username}: {additional_hours} часов")

    # Получаем активную сессию
    rental_manager = get_rental_manager()
    if not rental_manager:
        response_text = "❌ Система аренды не настроена."
        Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()
        return

    user_sessions = rental_manager.get_user_sessions(chat_id)
    active_session = None
    
    for session in user_sessions:
        if session.active and session.game_downloaded:
            active_session = session
            break

    if not active_session:
        response_text = "❌ Нет активной аренды для продления."
        Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()
        return

    # Продлеваем сессию
    active_session.end_time += additional_hours * 3600
    rental_manager.save_sessions()
    
    remaining = active_session.get_remaining_hours()
    response_text = (
        f"✅ Аренда продлена на {additional_hours} часов!\n\n"
        f"🎮 Игра: {active_session.game_name}\n"
        f"⏳ Осталось времени: {remaining:.1f} часов"
    )

    Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()


# Привязываем хендлеры к событиям
BIND_TO_NEW_MESSAGE = [
    handle_rental_select_game,
    handle_rental_downloaded,
    handle_rental_status,
    handle_rental_extend
]
BIND_TO_LAST_CHAT_MESSAGE_CHANGED = [
    handle_rental_select_game,
    handle_rental_downloaded,
    handle_rental_status,
    handle_rental_extend
]
