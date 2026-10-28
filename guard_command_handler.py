#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Хендлер для команды !code с интеграцией Gmail Guard.
Регистрируется через BIND_TO_NEW_MESSAGE / BIND_TO_LAST_CHAT_MESSAGE_CHANGED.
"""

from __future__ import annotations
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from cardinal import Cardinal

from FunPayAPI.updater.events import NewMessageEvent, LastChatMessageChangedEvent
from guard_handler import get_guard_handler
from purchase_checker import get_purchase_checker
from Utils import cardinal_tools
from threading import Thread

logger = logging.getLogger("GuardCommand")


def handle_guard_code_command(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent):
    """Обработчик команды !code — выдаёт Steam Guard код через Gmail интеграцию."""
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

    # Реагируем только на команду !code
    if mtext.replace("\n", "").strip().lower() != "!code":
        return

    logger.info(f"Команда !code от {username} в чате {chat_name}")

    # --- Проверка лимита попыток через purchase_checker ---
    pc = get_purchase_checker()
    if pc:
        can_request, deny_message = pc.can_request_code(chat_id, username)
        if not can_request:
            Thread(target=c.send_message, args=(chat_id, deny_message, chat_name), daemon=True).start()
            return
        pc.record_code_attempt(chat_id, username)
        remaining = pc.get_remaining_attempts(chat_id)
        attempts_line = f"\n📊 Осталось попыток: {'∞' if remaining == -1 else remaining}"
    else:
        attempts_line = ""

    # --- Попытка получить Guard код через Guard систему (Gmail или shared_secret) ---
    gh = get_guard_handler()
    code_generated = False

    # Определяем Steam-аккаунт по привязке чата (фикс бага: при нескольких аккаунтах
    # код раньше генерировался от первого попавшегося shared_secret).
    bound_account = None
    try:
        pc_local = get_purchase_checker()
        if pc_local:
            bound_account = pc_local.get_account_for_chat(chat_id)
    except Exception:
        bound_account = None
    if bound_account:
        logger.info(f"!code: чат {chat_id} привязан к аккаунту {bound_account}")

    if gh and gh.enabled:
        try:
            code = None
            if bound_account:
                code = gh.get_guard_code_for_account(bound_account)
            if not code:
                code = gh.get_guard_code(username)

            if code and not code.startswith("❌"):
                # ── Запускаем таймер оффлайн-сессии при первом !code ──────
                try:
                    from rental_system import get_rental_manager
                    rm = get_rental_manager()
                    if rm:
                        sess = rm.get_active_session_for_chat(chat_id)
                        if sess and sess.is_offline and sess.offline_code_used_at is None:
                            sess.mark_offline_code_used()
                            rm.save_sessions()
                        elif sess and sess.is_rental and sess.rental_code_used_at is None:
                            sess.mark_rental_code_used()
                            rm.save_sessions()
                except Exception:
                    pass
            if code and not code.startswith("❌"):
                response_text = (
                    f"🔑 Код Steam Guard: <code>{code}</code>\n"
                    f"⏱ Действителен ~30 секунд{attempts_line}"
                )
                code_generated = True
            else:
                if gh.use_shared_secret:
                    response_text = (
                        "⏳ Код не может быть сгенерирован (shared_secret).\n"
                        "Убедитесь, что аккаунт добавлен в систему.\n"
                        "Попробуйте снова или обратитесь к продавцу."
                    )
                else:
                    response_text = (
                        "⏳ Код ещё не пришёл на почту.\n"
                        "Попробуйте через 15–30 секунд — коды обновляются автоматически.\n"
                        "Если долго нет — напишите продавцу."
                    )
        except Exception as ex:
            logger.error(f"Ошибка получения Guard кода: {ex}")
            response_text = (
                "❌ Ошибка при получении кода Steam Guard. "
                "Попробуйте через 30 секунд или обратитесь к продавцу."
            )
    else:
        # Gmail не настроен — используем стандартный ответ из конфига auto_response
        if "!code" in c.AR_CFG and c.AR_CFG["!code"].getboolean("enabled", fallback=True):
            response_text = cardinal_tools.format_msg_text(c.AR_CFG["!code"]["response"], obj)
            response_text += attempts_line
        else:
            response_text = (
                "❌ Система Steam Guard не настроена. "
                "Обратитесь к продавцу для получения кода вручную."
            )

    Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()
    
    # --- Отправка уведомления владельцу бота при успешной генерации кода ---
    if code_generated and c.telegram and c.telegram.bot:
        try:
            owner_message = (
                f"🔔 Уведомление о Guard коде\n\n"
                f"👤 Пользователь: {username}\n"
                f"💬 Чат: {chat_name}\n"
                f"🔑 Код: {code}\n"
                f"⏱ Действителен ~30 секунд\n"
                f"📊 Осталось попыток: {'∞' if remaining == -1 else remaining}\n"
                f"🆔 Chat ID: {chat_id}"
            )
            
            # Отправляем владельцу бота если указан owner_chat_id
            if c.telegram.owner_chat_id:
                Thread(target=c.telegram.bot.send_message, 
                      args=(c.telegram.owner_chat_id, owner_message), 
                      daemon=True).start()
                logger.info(f"Уведомление о Guard коде отправлено владельцу: {username} -> {code}")
            else:
                # Если owner_chat_id не указан, отправляем в лог
                logger.info(f"GUARD CODE GENERATED: {username} -> {code} (Remaining: {remaining})")
                
        except Exception as ex:
            logger.error(f"Ошибка отправки уведомления владельцу: {ex}")


# Привязываем хендлер к событиям новых сообщений
BIND_TO_NEW_MESSAGE = [handle_guard_code_command]
BIND_TO_LAST_CHAT_MESSAGE_CHANGED = [handle_guard_code_command]
