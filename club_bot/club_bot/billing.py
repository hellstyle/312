#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль бухгалтерии клубного бота.

Ставки:
  rust  — 60 ₽/час
  cs    — 60 ₽/час

Скидка 10% от 6 часов.
Раздел прибыли: 50% владелец / 50% клуб.
"""

import json
import os
import threading
from datetime import datetime, date, timedelta
from typing import Optional
import logging

logger = logging.getLogger("ClubBot.Billing")

STORAGE_DIR = "club_bot/data"
LEDGER_FILE = f"{STORAGE_DIR}/ledger.json"

RATES = {
    "rust": 300,
    "cs":   300,
}
DISCOUNT_THRESHOLD  = 6
DISCOUNT_PERCENT    = 10
MULTI_ACC_DISCOUNT  = 15   # скидка % за 2 аккаунта
CURRENCY            = "₸"
OWNER_SHARE        = 0.5   # 50% владельцу
CLUB_SHARE         = 0.5   # 50% клубу

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Расчёт стоимости
# ---------------------------------------------------------------------------

def calc_price(pool: str, hours: float, acc_count: int = 1) -> dict:
    rate     = RATES.get(pool.lower(), 300)
    base     = rate * hours * acc_count
    # Скидка за часы
    disc_pct = DISCOUNT_PERCENT if hours >= DISCOUNT_THRESHOLD else 0
    # Скидка за 2 аккаунта (суммируется)
    if acc_count >= 2:
        disc_pct += MULTI_ACC_DISCOUNT
    discount = base * disc_pct / 100
    total    = base - discount
    owner    = round(total * OWNER_SHARE, 2)
    club     = round(total * CLUB_SHARE, 2)
    return {
        "rate":         rate,
        "hours":        hours,
        "acc_count":    acc_count,
        "base":         round(base, 2),
        "discount":     round(discount, 2),
        "discount_pct": disc_pct,
        "total":        round(total, 2),
        "owner_share":  owner,
        "club_share":   club,
    }


def format_receipt(pool: str, hours: float, acc_username: str) -> str:
    p = calc_price(pool, hours)
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
    txt += (
        f"\n✅ <b>Итого: {p['total']} {CURRENCY}</b>\n\n"
        f"💼 Владелец: <b>{p['owner_share']} {CURRENCY}</b>\n"
        f"🏢 Клуб: <b>{p['club_share']} {CURRENCY}</b>"
    )
    return txt


# ---------------------------------------------------------------------------
# Журнал
# ---------------------------------------------------------------------------

def _load_ledger() -> dict:
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
    pool    = session.get("pool", "?")
    hours   = session.get("hours", 0)
    acc     = session.get("acc_username", "?")
    player  = session.get("player_id", 0)
    price   = calc_price(pool, hours)
    day_key = date.today().isoformat()
    ledger  = _load_ledger()

    if day_key not in ledger:
        ledger[day_key] = {"sessions": [], "total": 0.0,
                           "owner_total": 0.0, "club_total": 0.0}

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
        "owner_share":  price["owner_share"],
        "club_share":   price["club_share"],
        "started_at":   session.get("started_at"),
        "ended_at":     session.get("ends_at"),
        "recorded_at":  datetime.now().isoformat(),
    }
    ledger[day_key]["sessions"].append(entry)
    ledger[day_key]["total"]       = round(sum(e["total"] for e in ledger[day_key]["sessions"]), 2)
    ledger[day_key]["owner_total"] = round(sum(e["owner_share"] for e in ledger[day_key]["sessions"]), 2)
    ledger[day_key]["club_total"]  = round(sum(e["club_share"] for e in ledger[day_key]["sessions"]), 2)
    _save_ledger(ledger)
    logger.info(f"Billing: {entry['session_id']} — {price['total']} ₽ (вл: {price['owner_share']} / клуб: {price['club_share']})")


# ---------------------------------------------------------------------------
# Отчёты
# ---------------------------------------------------------------------------

def _salary_block(owner_total: float, club_total: float, total: float) -> str:
    return (
        f"\n{'─'*20}\n"
        f"💼 <b>Зарплата владельца: {owner_total} {CURRENCY}</b>\n"
        f"🏢 <b>Зарплата клуба: {club_total} {CURRENCY}</b>\n"
        f"💰 <b>Общая выручка: {total} {CURRENCY}</b>"
    )


def report_day(day: Optional[str] = None) -> str:
    day_key = day or date.today().isoformat()
    ledger  = _load_ledger()
    data    = ledger.get(day_key)

    if not data or not data.get("sessions"):
        return f"📅 <b>{day_key}</b>\n\nСессий за этот день нет."

    sessions    = data["sessions"]
    total       = data["total"]
    owner_total = data.get("owner_total", round(total * OWNER_SHARE, 2))
    club_total  = data.get("club_total",  round(total * CLUB_SHARE, 2))

    by_pool = {}
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
            f"<b>{round(stats['total'],2)} {CURRENCY}</b>"
        )
    lines.append(f"📊 Всего сессий: {len(sessions)}")
    lines.append(_salary_block(owner_total, club_total, total))
    return "\n".join(lines)


def report_period(days: int = 7) -> str:
    ledger        = _load_ledger()
    today         = date.today()
    grand_total   = 0.0
    grand_owner   = 0.0
    grand_club    = 0.0
    session_count = 0
    lines = [f"📊 <b>Отчёт за {days} дней</b>\n"]

    for i in range(days - 1, -1, -1):
        day_key = (today - timedelta(days=i)).isoformat()
        data    = ledger.get(day_key)
        if not data or not data.get("sessions"):
            lines.append(f"📅 {day_key}: —")
            continue
        day_total   = data["total"]
        day_owner   = data.get("owner_total", round(day_total * OWNER_SHARE, 2))
        day_club    = data.get("club_total",  round(day_total * CLUB_SHARE, 2))
        cnt         = len(data["sessions"])
        grand_total   += day_total
        grand_owner   += day_owner
        grand_club    += day_club
        session_count += cnt
        lines.append(
            f"📅 {day_key}: {cnt} сес. — "
            f"<b>{day_total} {CURRENCY}</b> "
            f"(вл: {day_owner} / клуб: {day_club})"
        )

    lines.append(f"📊 Всего сессий: {session_count}")
    lines.append(_salary_block(
        round(grand_owner, 2),
        round(grand_club, 2),
        round(grand_total, 2)
    ))
    return "\n".join(lines)


def report_detailed_day(day: Optional[str] = None) -> list[str]:
    day_key = day or date.today().isoformat()
    ledger  = _load_ledger()
    data    = ledger.get(day_key)

    if not data or not data.get("sessions"):
        return [f"📅 <b>{day_key}</b>\n\nСессий нет."]

    total       = data["total"]
    owner_total = data.get("owner_total", round(total * OWNER_SHARE, 2))
    club_total  = data.get("club_total",  round(total * CLUB_SHARE, 2))

    msgs = [f"📅 <b>Детальный отчёт за {day_key}</b>\n"]
    for i, e in enumerate(data["sessions"], 1):
        disc = f" (−{e['discount']} {CURRENCY})" if e.get("discount") else ""
        msgs.append(
            f"{i}. {e['pool'].upper()} | {e['acc_username']}\n"
            f"   👤 Игрок: {e['player_id']}\n"
            f"   ⏱ {e['hours']} ч × {e['rate']} {CURRENCY}{disc}\n"
            f"   ✅ {e['total']} {CURRENCY}  "
            f"(вл: {e.get('owner_share', round(e['total']*OWNER_SHARE,2))} / "
            f"клуб: {e.get('club_share', round(e['total']*CLUB_SHARE,2))})"
        )
    msgs.append(_salary_block(owner_total, club_total, total))

    result, current = [], msgs[0]
    for line in msgs[1:]:
        if len(current) + len(line) + 1 > 4000:
            result.append(current)
            current = line
        else:
            current += "\n" + line
    result.append(current)
    return result


def reset_billing():
    """Полный сброс журнала бухгалтерии."""
    if os.path.exists(LEDGER_FILE):
        os.remove(LEDGER_FILE)
    logger.info("Billing: журнал сброшен")
