#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль бухгалтерии клубного бота.

Ставки:
  rust  — 300 руб/час
  cs    — 250 руб/час

Скидка 10% от 6 часов и выше.
"""

import json
import os
import threading
from datetime import datetime, date
from typing import Optional
import logging

logger = logging.getLogger("ClubBot.Billing")

STORAGE_DIR    = "club_bot/data"
LEDGER_FILE    = f"{STORAGE_DIR}/ledger.json"

RATES = {
    "rust": 300,
    "cs":   250,
}
DISCOUNT_THRESHOLD = 6
DISCOUNT_PERCENT   = 10
CURRENCY           = "₸"

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Расчёт стоимости
# ---------------------------------------------------------------------------

def calc_price(pool: str, hours: float) -> dict:
    """
    Возвращает словарь:
      rate        — ставка за час
      hours       — кол-во часов
      base        — базовая стоимость
      discount    — сумма скидки
      discount_pct— процент скидки
      total       — итого к оплате
    """
    rate     = RATES.get(pool.lower(), 300)
    base     = rate * hours
    disc_pct = DISCOUNT_PERCENT if hours >= DISCOUNT_THRESHOLD else 0
    discount = base * disc_pct / 100
    total    = base - discount
    return {
        "rate":         rate,
        "hours":        hours,
        "base":         round(base, 2),
        "discount":     round(discount, 2),
        "discount_pct": disc_pct,
        "total":        round(total, 2),
    }


def format_receipt(pool: str, hours: float, acc_username: str) -> str:
    """Возвращает текстовый чек."""
    p   = calc_price(pool, hours)
    txt = (
        f"🧾 <b>Чек аренды</b>\n\n"
        f"🎮 Игра: <b>{pool.upper()}</b>\n"
        f"👤 Аккаунт: <code>{acc_username}</code>\n"
        f"⏱ Время: <b>{p['hours']} ч</b>\n"
        f"💰 Ставка: {p['rate']} {CURRENCY}/ч\n"
        f"📊 База: {p['base']} {CURRENCY}\n"
    )
    if p["discount_pct"]:
        txt += f"🎁 Скидка {p['discount_pct']}%: −{p['discount']} {CURRENCY}\n"
    txt += f"\n✅ <b>Итого: {p['total']} {CURRENCY}</b>"
    return txt


# ---------------------------------------------------------------------------
# Журнал операций
# ---------------------------------------------------------------------------

def _load_ledger() -> dict:
    """Загружает журнал с диска."""
    try:
        if os.path.exists(LEDGER_FILE):
            with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"_load_ledger: {e}")
    return {}


def _save_ledger(ledger: dict):
    os.makedirs(STORAGE_DIR, exist_ok=True)
    with _lock:
        with open(LEDGER_FILE, "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=2)


def record_session(session: dict):
    """
    Записывает завершённую сессию в журнал.
    session — словарь сессии из sessions.json
    """
    pool     = session.get("pool", "?")
    hours    = session.get("hours", 0)
    acc      = session.get("acc_username", "?")
    player   = session.get("player_id", 0)
    started  = session.get("started_at")
    ended    = session.get("ends_at")

    price    = calc_price(pool, hours)
    day_key  = date.today().isoformat()

    ledger   = _load_ledger()
    if day_key not in ledger:
        ledger[day_key] = {"sessions": [], "total": 0.0}

    entry = {
        "session_id":   session.get("session_id", ""),
        "pool":         pool,
        "acc_username": acc,
        "player_id":    player,
        "hours":        hours,
        "rate":         price["rate"],
        "base":         price["base"],
        "discount":     price["discount"],
        "discount_pct": price["discount_pct"],
        "total":        price["total"],
        "started_at":   started,
        "ended_at":     ended,
        "recorded_at":  datetime.now().isoformat(),
    }
    ledger[day_key]["sessions"].append(entry)
    ledger[day_key]["total"] = round(
        sum(e["total"] for e in ledger[day_key]["sessions"]), 2
    )
    _save_ledger(ledger)
    logger.info(f"Billing: записана сессия {entry['session_id']} — {price['total']} руб")


# ---------------------------------------------------------------------------
# Отчёты
# ---------------------------------------------------------------------------

def report_day(day: Optional[str] = None) -> str:
    """
    Отчёт за день. day — строка 'YYYY-MM-DD', по умолчанию сегодня.
    """
    day_key = day or date.today().isoformat()
    ledger  = _load_ledger()
    data    = ledger.get(day_key)

    if not data or not data.get("sessions"):
        return f"📅 <b>{day_key}</b>\n\nСессий за этот день нет."

    sessions = data["sessions"]
    total    = data["total"]

    # Группируем по пулам
    by_pool  = {}
    for e in sessions:
        p = e["pool"].upper()
        if p not in by_pool:
            by_pool[p] = {"count": 0, "hours": 0.0, "total": 0.0}
        by_pool[p]["count"] += 1
        by_pool[p]["hours"] += e["hours"]
        by_pool[p]["total"] += e["total"]

    lines = [f"📅 <b>Отчёт за {day_key}</b>\n"]
    for pool_name, stats in by_pool.items():
        lines.append(
            f"🎮 {pool_name}: {stats['count']} сес. × "
            f"{round(stats['hours'],1)} ч = "
            f"<b>{round(stats['total'],2)} ₸</b>"
        )

    lines.append(f"\n💰 <b>Итого за день: {total} ₸</b>")
    lines.append(f"📊 Всего сессий: {len(sessions)}")
    return "\n".join(lines)


def report_period(days: int = 7) -> str:
    """Отчёт за последние N дней."""
    from datetime import timedelta
    ledger = _load_ledger()
    today  = date.today()
    grand_total = 0.0
    session_count = 0
    lines  = [f"📊 <b>Отчёт за {days} дней</b>\n"]

    for i in range(days - 1, -1, -1):
        day_key = (today - timedelta(days=i)).isoformat()
        data    = ledger.get(day_key)
        if not data or not data.get("sessions"):
            lines.append(f"📅 {day_key}: —")
            continue
        day_total = data["total"]
        cnt       = len(data["sessions"])
        grand_total   += day_total
        session_count += cnt
        lines.append(f"📅 {day_key}: {cnt} сес. — <b>{day_total} руб</b>")

    lines.append(f"\n💰 <b>Итого за период: {round(grand_total,2)} ₸</b>")
    lines.append(f"📊 Всего сессий: {session_count}")
    return "\n".join(lines)


def report_detailed_day(day: Optional[str] = None) -> list[str]:
    """
    Детальный отчёт за день — список строк (по одной на сессию).
    Возвращает список сообщений (разбито на части если много).
    """
    day_key  = day or date.today().isoformat()
    ledger   = _load_ledger()
    data     = ledger.get(day_key)

    if not data or not data.get("sessions"):
        return [f"📅 <b>{day_key}</b>\n\nСессий нет."]

    msgs = [f"📅 <b>Детальный отчёт за {day_key}</b>\n"]
    for i, e in enumerate(data["sessions"], 1):
        disc = f" (−{e['discount']} ₸)" if e.get("discount") else ""
        msgs.append(
            f"{i}. {e['pool'].upper()} | {e['acc_username']}\n"
            f"   👤 Игрок: {e['player_id']}\n"
            f"   ⏱ {e['hours']} ч × {e['rate']} ₸{disc}\n"
            f"   ✅ {e['total']} ₸"
        )
    msgs.append(f"\n💰 <b>Итого: {data['total']} ₸</b>")

    # Объединяем в блоки по 4096 символов
    result, current = [], msgs[0]
    for line in msgs[1:]:
        if len(current) + len(line) + 1 > 4000:
            result.append(current)
            current = line
        else:
            current += "\n" + line
    result.append(current)
    return result
