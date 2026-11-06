#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
alert_notifier — повторяющиеся уведомления в Telegram.

Логика:
  1. Событие → таймер 2 минуты.
  2. Если не нажал Стоп — волна: 10 сообщений по 3 с паузой 1 сек.
  3. Пауза 2 минуты → повтор.
  4. Стоп — немедленная остановка (даже внутри волны).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cardinal import Cardinal

logger = logging.getLogger("AlertNotifier")

# {alert_id: threading.Event}
_active_alerts: dict[str, threading.Event] = {}
_lock = threading.Lock()

# Тихий период после нажатия Стоп: {chat_id_prefix: timestamp}
# Пока время не истекло — новые алерты для этого чата не запускаются
_silence_until: dict[str, float] = {}
_silence_lock = threading.Lock()

SILENCE_AFTER_STOP = 10 * 60  # 10 минут тишины после нажатия Стоп

INITIAL_DELAY  = 0            # без задержки — первая волна сразу
REPEAT_INTERVAL = 2 * 60  # пауза между волнами
BURST_TOTAL    = 10       # сообщений в волне
BURST_SIZE     = 3        # сообщений за раз
BURST_PAUSE    = 1        # сек между пачками


def _is_silenced(alert_id: str) -> bool:
    """Возвращает True если для этого alert_id ещё действует тихий период."""
    with _silence_lock:
        until = _silence_until.get(alert_id)
    if until and time.time() < until:
        remaining = int(until - time.time())
        logger.info(f"Alert [{alert_id}] в тихом периоде ещё {remaining}с — пропускаем")
        return True
    return False


def _set_silence(alert_id: str):
    """Устанавливает тихий период для alert_id на SILENCE_AFTER_STOP секунд."""
    with _silence_lock:
        _silence_until[alert_id] = time.time() + SILENCE_AFTER_STOP
    logger.info(f"Alert [{alert_id}] тихий период {SILENCE_AFTER_STOP // 60} мин")


def start_alert(
    cardinal: "Cardinal",
    alert_id: str,
    title: str = "🔔 Напоминание",
    body: str = "",
) -> str:
    if not cardinal.telegram or not cardinal.telegram.bot:
        return ""
    owner_id = getattr(cardinal.telegram, "owner_chat_id", None)
    if not owner_id:
        return ""

    # Не запускаем если тихий период ещё действует
    if _is_silenced(alert_id):
        return ""

    # Останавливаем предыдущий алерт с тем же ID и ждём чуть чтобы поток завершился
    _stop_and_wait(alert_id)

    stop_event = threading.Event()
    with _lock:
        _active_alerts[alert_id] = stop_event

    def _send(text, kb=None):
        """Отправляет одно сообщение, возвращает False если стоп."""
        if stop_event.is_set():
            return False
        try:
            cardinal.telegram.bot.send_message(owner_id, text, reply_markup=kb)
        except Exception as ex:
            logger.error(f"Alert [{alert_id}] send error: {ex}")
        return not stop_event.is_set()

    def _loop():
        # Ждём перед первой волной
        if stop_event.wait(timeout=INITIAL_DELAY):
            _set_silence(alert_id)
            _cleanup(alert_id)
            logger.info(f"Alert [{alert_id}] остановлен до первой волны")
            return

        wave = 0
        while not stop_event.is_set():
            wave += 1
            _run_wave(wave, stop_event, alert_id, title, body, cardinal, owner_id)
            # Пауза между волнами
            stop_event.wait(timeout=REPEAT_INTERVAL)

        # Подтверждение
        try:
            cardinal.telegram.bot.send_message(
                owner_id,
                f"✅ Уведомления остановлены\n🔕 Тихий режим на {SILENCE_AFTER_STOP // 60} мин",
                disable_notification=True
            )
        except Exception:
            pass
        _set_silence(alert_id)
        _cleanup(alert_id)
        logger.info(f"Alert [{alert_id}] завершён после {wave} волн")

    t = threading.Thread(target=_loop, name=f"alert-{alert_id}", daemon=True)
    t.start()
    logger.info(f"Alert [{alert_id}] запущен")
    return alert_id


def _run_wave(wave: int, stop_event: threading.Event, alert_id: str,
              title: str, body: str, cardinal, owner_id):
    """Отправляет одну волну (10 сообщений по 3)."""
    from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B
    from tg_bot.CBT import STOP_ALERT

    kb = K().add(B("🔕 Стоп", callback_data=f"{STOP_ALERT}:{alert_id}"))
    sent = 0

    while sent < BURST_TOTAL:
        if stop_event.is_set():
            return
        batch = min(BURST_SIZE, BURST_TOTAL - sent)
        for i in range(batch):
            if stop_event.is_set():
                return
            num = sent + i + 1
            is_last = (num == BURST_TOTAL)
            text = (
                f"{title}\n\n"
                f"{body}\n\n"
                f"🔁 Волна #{wave} · {num}/{BURST_TOTAL}"
            )
            try:
                cardinal.telegram.bot.send_message(
                    owner_id, text,
                    parse_mode="HTML",
                    reply_markup=kb if is_last else None
                )
            except Exception as ex:
                logger.error(f"Alert [{alert_id}] wave send: {ex}")
        sent += batch
        if sent < BURST_TOTAL:
            # Пауза между пачками — прерываемая
            stop_event.wait(timeout=BURST_PAUSE)

    logger.info(f"Alert [{alert_id}] волна #{wave} отправлена")


def _stop_and_wait(alert_id: str, timeout: float = 0.3):
    """Ставит флаг и немного ждёт чтобы поток успел выйти из цикла."""
    with _lock:
        event = _active_alerts.get(alert_id)
    if event:
        event.set()
        time.sleep(timeout)


def _cleanup(alert_id: str):
    with _lock:
        _active_alerts.pop(alert_id, None)


def stop_alert(alert_id: str) -> bool:
    with _lock:
        event = _active_alerts.get(alert_id)
    if event and not event.is_set():
        event.set()
        return True
    return False


def stop_all_alerts() -> int:
    with _lock:
        ids = list(_active_alerts.keys())
    for aid in ids:
        stop_alert(aid)
    return len(ids)


def list_alerts() -> list[str]:
    with _lock:
        return list(_active_alerts.keys())


def register_stop_handler(cardinal: "Cardinal"):
    """Заглушка — хендлер зарегистрирован в TGBot._on_stop_alert."""
    logger.info("AlertNotifier: register_stop_handler (stub)")
