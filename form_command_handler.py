#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Хендлер для обработки форм заказов Amazing RP.
Регистрируется через BIND_TO_NEW_MESSAGE / BIND_TO_LAST_CHAT_MESSAGE_CHANGED.

Логика:
  1. Покупатель получает авто-ответ на заказ с просьбой заполнить форму.
  2. Покупатель заполняет форму прямо в чате.
  3. Бот распознаёт форму, сохраняет её и:
     - отправляет покупателю подтверждение
     - отправляет продавцу уведомление в Telegram с кнопкой "Ответить"
  4. Если форма не заполнена — бот молчит, продавца не беспокоит.
"""

from __future__ import annotations
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from cardinal import Cardinal

from FunPayAPI.updater.events import NewMessageEvent, LastChatMessageChangedEvent
from form_processor import get_form_processor
from tg_bot import utils as tg_utils, keyboards
from threading import Thread

logger = logging.getLogger("FormCommand")


def handle_form_message(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent):
    """
    Слушает входящие сообщения и распознаёт заполненные формы Amazing RP.
    """
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

    # Пропускаем команды — их обрабатывает auto_response
    if mtext.strip().startswith("!"):
        return

    form_processor = get_form_processor()
    if not form_processor:
        return

    # Пробуем распарсить форму
    form_data = form_processor.parse_form(mtext.strip())
    if not form_data:
        return  # Не форма — молчим, продавца не беспокоим

    # Валидируем
    is_valid, error_message = form_processor.validate_form(form_data)
    if not is_valid:
        response_text = (
            f"❌ Ошибка в форме: {error_message}\n\n"
            "Исправьте и отправьте снова. Для примера — напишите !form"
        )
        Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()
        return

    # Определяем название услуги — ищем последний известный заказ от этого пользователя
    lot_name = _get_lot_name_for_user(c, username)

    # Сохраняем форму
    form_processor.submit_form(chat_id, username, form_data, lot_name)

    # --- Покупателю: чистое подтверждение ---
    form_summary = form_processor.get_form_summary(form_data, lot_name)
    buyer_text = (
        f"✅ Форма принята!\n\n"
        f"{form_summary}\n\n"
        "Я свяжусь с вами в ближайшее время для уточнения деталей 👍"
    )
    Thread(target=c.send_message, args=(chat_id, buyer_text, chat_name), daemon=True).start()

    # --- Продавцу: уведомление в Telegram ---
    _notify_seller(c, chat_id, username, form_summary, lot_name)

    logger.info(f"Форма Amazing RP принята от {username} (chat_id: {chat_id}, лот: {lot_name!r})")


def _get_lot_name_for_user(c: Cardinal, username: str) -> str:
    """
    Пытается найти название последнего лота по покупателю из сохранённых заказов runner'а.
    Возвращает пустую строку если не удалось определить.
    """
    try:
        saved = getattr(c.runner, "saved_orders", None)
        if not saved:
            return ""
        # saved_orders — dict[order_id, OrderShortcut], ищем последний от этого покупателя
        for order in reversed(list(saved.values())):
            if order.buyer_username == username:
                return order.description or ""
    except Exception as ex:
        logger.debug(f"Не удалось определить лот для {username}: {ex}")
    return ""


def _notify_seller(c: Cardinal, chat_id: int, username: str, form_summary: str, lot_name: str):
    """
    Отправляет уведомление продавцу в Telegram о заполненной форме.
    """
    if not c.telegram:
        return

    lot_line = f"\n🎮 <b>Услуга:</b> <code>{lot_name}</code>" if lot_name else ""
    text = (
        f"📋 <b>Новая заявка на Amazing RP!</b>\n"
        f"👤 <b>Покупатель:</b> <code>{username}</code>{lot_line}\n\n"
        f"{form_summary}"
    )

    kb = keyboards.reply(chat_id, username)

    Thread(
        target=c.telegram.send_notification,
        args=(text,),
        kwargs={
            "keyboard": kb,
            "notification_type": tg_utils.NotificationTypes.new_order,
        },
        daemon=True,
    ).start()


# Привязываем хендлер к событиям новых сообщений
BIND_TO_NEW_MESSAGE = [handle_form_message]
BIND_TO_LAST_CHAT_MESSAGE_CHANGED = [handle_form_message]
