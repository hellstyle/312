#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refund_handler — автовозврат средств покупателю.

Функции:
  1. Автовозврат по сообщению покупателя — если в чате появляются слова из
     списка (вернуть, возврат, refund, !refund и т.д.) — бот автоматически
     делает возврат по последнему заказу этого чата.
  2. Команда /refund <order_id> в Telegram-боте владельца — ручной возврат
     по любому ID заказа.
  3. Привязка чат → заказ при выдаче товара — без неё автовозврат не работает.

Настройки в configs/_main.cfg:
  [AutoRefund]
  enabled = 1
  keywords = вернуть,возврат,refund,!refund,верните,хочу вернуть,не работает
  notify_buyer = 1
  notify_buyer_text = ✅ Возврат средств за заказ #{order_id} оформлен. Деньги вернутся на баланс FunPay.
  notify_owner = 1
"""

from __future__ import annotations

import logging
import time
from threading import Thread
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cardinal import Cardinal

from FunPayAPI.updater.events import NewMessageEvent, LastChatMessageChangedEvent, NewOrderEvent

logger = logging.getLogger("RefundHandler")

# Хранилище: chat_id → order_id последнего заказа этого чата
_chat_to_order: dict[int | str, str] = {}


def register_order_for_chat(chat_id: int | str, order_id: str):
    """Запоминает ID последнего заказа для чата. Вызывается при выдаче товара."""
    _chat_to_order[chat_id] = order_id
    logger.debug(f"Чат {chat_id} → заказ {order_id}")


def get_order_for_chat(chat_id: int | str) -> str | None:
    """Возвращает ID последнего заказа для чата или None."""
    return _chat_to_order.get(chat_id)


def _get_config(cardinal: "Cardinal") -> dict:
    """Читает настройки [AutoRefund] из конфига, с дефолтами."""
    cfg = cardinal.MAIN_CFG
    if not cfg.has_section("AutoRefund"):
        return {
            "enabled": False,
            "keywords": [],
            "notify_buyer": True,
            "notify_buyer_text": "✅ Возврат средств за заказ #{order_id} оформлен.",
            "notify_owner": True,
        }
    return {
        "enabled": cfg.getboolean("AutoRefund", "enabled", fallback=False),
        "keywords": [k.strip().lower() for k in
                     cfg.get("AutoRefund", "keywords",
                              fallback="вернуть,возврат,refund,!refund,верните,хочу вернуть").split(",")
                     if k.strip()],
        "notify_buyer": cfg.getboolean("AutoRefund", "notify_buyer", fallback=True),
        "notify_buyer_text": cfg.get("AutoRefund", "notify_buyer_text",
                                      fallback="✅ Возврат средств за заказ #{order_id} оформлен. "
                                               "Деньги вернутся на баланс FunPay."),
        "notify_owner": cfg.getboolean("AutoRefund", "notify_owner", fallback=True),
    }


def _do_refund(cardinal: "Cardinal", order_id: str, chat_id: int | str,
               buyer_username: str | None, source: str = "auto") -> bool:
    """
    Выполняет возврат. Возвращает True при успехе.

    :param source: "auto" (из сообщения покупателя) | "manual" (из Telegram команды)
    """
    cfg = _get_config(cardinal)
    attempts = 3
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            cardinal.account.refund(order_id)
            logger.info(f"Возврат по заказу {order_id} выполнен (источник: {source}, "
                        f"чат: {chat_id}, покупатель: {buyer_username})")
            break
        except Exception as e:
            last_error = e
            logger.warning(f"Попытка {attempt}/{attempts} возврата {order_id} не удалась: {e}")
            time.sleep(1)
    else:
        logger.error(f"Не удалось оформить возврат по заказу {order_id}: {last_error}")
        # Уведомляем владельца об ошибке
        if cfg["notify_owner"] and cardinal.telegram and cardinal.telegram.bot:
            _tg_notify(cardinal,
                       f"❌ Ошибка автовозврата\n\n"
                       f"Заказ: <code>{order_id}</code>\n"
                       f"Покупатель: {buyer_username}\n"
                       f"Ошибка: <code>{last_error}</code>")
        return False

    # Уведомляем покупателя
    if cfg["notify_buyer"] and chat_id:
        text = cfg["notify_buyer_text"].replace("{order_id}", order_id)
        Thread(target=cardinal.send_message,
               args=(chat_id, text, buyer_username),
               kwargs={"watermark": False},
               daemon=True).start()

    # Уведомляем владельца
    if cfg["notify_owner"] and cardinal.telegram and cardinal.telegram.bot:
        _tg_notify(cardinal,
                   f"↩️ Возврат оформлен\n\n"
                   f"Заказ: <code>{order_id}</code>\n"
                   f"Покупатель: {buyer_username or '?'}\n"
                   f"Источник: {'авто' if source == 'auto' else 'вручную'}")
    return True


def _tg_notify(cardinal: "Cardinal", text: str):
    """Отправляет уведомление владельцу в Telegram."""
    try:
        owner_id = getattr(cardinal.telegram, "owner_chat_id", None)
        if owner_id and cardinal.telegram.bot:
            Thread(target=cardinal.telegram.bot.send_message,
                   args=(owner_id, text),
                   daemon=True).start()
    except Exception as e:
        logger.error(f"Ошибка отправки TG уведомления: {e}")


# ---------------------------------------------------------------------------
# Хендлер новых сообщений FunPay — автовозврат по ключевым словам
# ---------------------------------------------------------------------------
def auto_refund_message_handler(c: "Cardinal",
                                e: NewMessageEvent | LastChatMessageChangedEvent):
    """
    Слушает сообщения покупателей. Если сообщение содержит ключевое слово
    возврата — автоматически оформляет возврат по последнему заказу этого чата.
    """
    cfg = _get_config(c)
    if not cfg["enabled"]:
        return

    # Определяем режим и извлекаем данные
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return
        msg_text = str(e.message).strip().lower()
        chat_id = e.message.chat_id
        chat_name = e.message.chat_name
        author = e.message.author
        # Пропускаем свои сообщения
        if author == c.account.username:
            return
    else:
        msg_text = str(e.chat).strip().lower()
        chat_id = e.chat.id
        chat_name = e.chat.name
        author = chat_name

    # Проверяем ключевые слова
    if not any(kw in msg_text for kw in cfg["keywords"]):
        return

    # Ищем заказ этого чата
    order_id = get_order_for_chat(chat_id)
    if not order_id:
        logger.info(f"Автовозврат: покупатель {author} ({chat_id}) запросил возврат, "
                    f"но заказ для этого чата не найден.")
        # Отвечаем покупателю
        Thread(target=c.send_message,
               args=(chat_id,
                     "⚠️ Не удалось найти ваш заказ для возврата.\n"
                     "Пожалуйста, укажите ID заказа или обратитесь к продавцу.",
                     chat_name),
               kwargs={"watermark": False},
               daemon=True).start()
        return

    logger.info(f"Автовозврат: покупатель {author} ({chat_id}) запросил возврат "
                f"по заказу {order_id}.")

    # Выполняем возврат в отдельном потоке
    Thread(target=_do_refund,
           args=(c, order_id, chat_id, author, "auto"),
           daemon=True).start()


# ---------------------------------------------------------------------------
# Хендлер новых заказов — сохраняем привязку чат → заказ
# ---------------------------------------------------------------------------
def track_order_handler(c: "Cardinal", e: NewOrderEvent):
    """
    При каждом новом заказе сохраняет привязку chat_id → order_id.
    Нужно для автовозврата по сообщению покупателя.
    """
    try:
        chat_id = e.order.chat_id
        order_id = e.order.id
        register_order_for_chat(chat_id, order_id)
    except Exception as ex:
        logger.error(f"track_order_handler: {ex}")


# ---------------------------------------------------------------------------
# Публичная функция для вызова из Telegram бота командой /refund <order_id>
# ---------------------------------------------------------------------------
def manual_refund(cardinal: "Cardinal", order_id: str, chat_id: int | str | None = None,
                  buyer_username: str | None = None) -> bool:
    """
    Ручной возврат по команде владельца.

    :param order_id: ID заказа (без #).
    :param chat_id: ID чата покупателя (опц., для уведомления).
    :param buyer_username: никнейм покупателя (опц., для логов).
    :return: True при успехе.
    """
    order_id = order_id.lstrip("#")
    return _do_refund(cardinal, order_id, chat_id, buyer_username, source="manual")


# ---------------------------------------------------------------------------
# Привязка к событиям
# ---------------------------------------------------------------------------
BIND_TO_NEW_MESSAGE = [auto_refund_message_handler]
BIND_TO_LAST_CHAT_MESSAGE_CHANGED = [auto_refund_message_handler]
BIND_TO_NEW_ORDER = [track_order_handler]
