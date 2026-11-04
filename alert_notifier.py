#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
alert_notifier — повторяющиеся уведомления в Telegram.

Логика:
  1. Событие (новое сообщение / заказ) → запускается таймер 3 минуты.
  2. Если за 3 минуты владелец не нажал «Стоп» — начинается спам каждые 2 минуты.
  3. Спам идёт БЕЗ остановки пока не нажать кнопку «🔕 Стоп».

Использование:
    from alert_notifier import start_alert
    start_alert(cardinal=c, alert_id="msg_12345", title="💬 Сообщение!", body="Vasya: привет")
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cardinal import Cardinal

logger = logging.getLogger("AlertNotifier")

# Реестр активных алертов: {alert_id: threading.Event}
_active_alerts: dict[str, threading.Event] = {}
_lock = threading.Lock()

# Задержка перед первым повтором (секунды)
INITIAL_DELAY = 2 * 60   # 2 минуты
# Интервал между повторами после начала спама (секунды)
REPEAT_INTERVAL = 2 * 60  # 2 минуты


def start_alert(
    cardinal: "Cardinal",
    alert_id: str | None = None,
    title: str = "🔔 Напоминание",
    body: str = "",
) -> str:
    """
    Запускает цикл уведомлений.
    Первое уведомление — через 3 минуты, затем каждые 2 минуты без остановки.

    :param cardinal: экземпляр Cardinal
    :param alert_id: уникальный ID (если None — генерируется автоматически)
    :param title: заголовок уведомления
    :param body: текст уведомления
    :return: alert_id
    """
    if not cardinal.telegram or not cardinal.telegram.bot:
        return ""

    owner_id = getattr(cardinal.telegram, "owner_chat_id", None)
    if not owner_id:
        return ""

    if alert_id is None:
        alert_id = str(uuid.uuid4())[:8]

    # Если алерт с таким ID уже запущен — останавливаем старый
    stop_alert(alert_id)

    stop_event = threading.Event()
    with _lock:
        _active_alerts[alert_id] = stop_event

    def _loop():
        # Ждём 3 минуты перед первым повтором
        stopped = stop_event.wait(timeout=INITIAL_DELAY)
        if stopped:
            # Владелец нажал стоп за 3 минуты — не спамим
            with _lock:
                _active_alerts.pop(alert_id, None)
            logger.info(f"Alert [{alert_id}] остановлен до первого повтора")
            return

        # Начинаем спам
        count = 0
        while not stop_event.is_set():
            count += 1
            try:
                from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B
                from tg_bot.CBT import STOP_ALERT

                # Шлём 10 сообщений пачками по 3 с паузой 1 сек
                BURST_TOTAL = 10
                BURST_SIZE  = 3
                BURST_PAUSE = 1  # сек между пачками

                kb = K().add(
                    B("🔕 Стоп", callback_data=f"{STOP_ALERT}:{alert_id}")
                )
                sent = 0
                while sent < BURST_TOTAL and not stop_event.is_set():
                    batch = min(BURST_SIZE, BURST_TOTAL - sent)
                    for i in range(batch):
                        if stop_event.is_set():
                            break
                        is_last = (sent + i + 1 == BURST_TOTAL)
                        text = (
                            f"{title}\n\n"
                            f"{body}\n\n"
                            f"🔁 Волна #{count} · сообщение {sent + i + 1}/{BURST_TOTAL}"
                        )
                        # Кнопку добавляем только к последнему в пачке
                        cardinal.telegram.bot.send_message(
                            owner_id, text,
                            reply_markup=kb if is_last else None
                        )
                    sent += batch
                    if sent < BURST_TOTAL:
                        stop_event.wait(timeout=BURST_PAUSE)

                logger.info(f"Alert [{alert_id}] волна #{count} отправлена ({BURST_TOTAL} сообщений)")
            except Exception as ex:
                logger.error(f"Alert [{alert_id}] ошибка: {ex}")

            stop_event.wait(timeout=REPEAT_INTERVAL)

        # Подтверждение остановки
        try:
            cardinal.telegram.bot.send_message(
                owner_id,
                f"✅ Уведомления остановлены"
            )
        except Exception:
            pass

        with _lock:
            _active_alerts.pop(alert_id, None)
        logger.info(f"Alert [{alert_id}] остановлен после {count} повторов")

    t = threading.Thread(target=_loop, name=f"alert-{alert_id}", daemon=True)
    t.start()
    logger.info(f"Alert [{alert_id}] запущен (задержка {INITIAL_DELAY}s, интервал {REPEAT_INTERVAL}s)")
    return alert_id


def stop_alert(alert_id: str) -> bool:
    """Останавливает алерт по ID."""
    with _lock:
        event = _active_alerts.get(alert_id)
    if event:
        event.set()
        return True
    return False


def stop_all_alerts() -> int:
    """Останавливает все активные алерты."""
    with _lock:
        ids = list(_active_alerts.keys())
    for aid in ids:
        stop_alert(aid)
    return len(ids)


def list_alerts() -> list[str]:
    """Список ID активных алертов."""
    with _lock:
        return list(_active_alerts.keys())


def register_stop_handler(cardinal: "Cardinal"):
    """
    Хендлер кнопки «Стоп» теперь зарегистрирован напрямую в TGBot._on_stop_alert.
    Эта функция оставлена для обратной совместимости с cardinal.py.
    """
    logger.info("AlertNotifier: хендлер зарегистрирован в TGBot (register_stop_handler — заглушка)")
