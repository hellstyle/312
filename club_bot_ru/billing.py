#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Бухгалтерия российского клубного бота.
Ставка: 60 ₽/час для всех игр.
Скидка 10% от 6 часов, скидка 15% за 2 аккаунта.
"""
import json, os, threading, logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger("ClubBotRU.Billing")

STORAGE_DIR   = "club_bot_ru/data"
LEDGER_FILE   = f"{STORAGE_DIR}/ledger.json"

RATES = {
    "rust": 60,
    "cs":   60,
}
DISCOUNT_THRESHOLD = 6
DISCOUNT_PERCENT   = 10
MULTI_ACC_DISCOUNT = 15
CURRENCY           = "₽"
OWNER_SHARE        = 0.5
CLUB_SHARE         = 0.5

_lock = threading.Lock()


def calc_price(pool: str, hours: float, acc_count: int = 1) -> dict:
    rate     = RATES.get(pool.lower(), 60)
    base     = rate * hours * acc_count
    disc_pct = DISCOUNT_PERCENT if hours >= DISCOUNT_THRESHOLD else 0
    if acc_count >= 2:
        disc_pct += MULTI_ACC_DISCOUNT
    discount = base * disc_pct / 100
    total    = base - discount
    return {
        "rate":         rate,
        "hours":        hours,
        "acc_count":    acc_count,
        "base":         round(base, 2),
        "discount":     round(discount, 2),
        "discount_pct": disc_pct,
        "total":        round(total, 2),
        "owner_share":  round(total * OWNER_SHARE, 2),
        "club_share":   round(total * CLUB_SHARE, 2),
    }


def format_receipt(pool: str, hours: float, acc_username: str, acc_count: int = 1) -> str:
    p   = calc_price(pool, hours, acc_count)
    txt = (
        f"🧾 <b>Чек аренды</b>\n\n"
        f"🎮 Игра: <b>{pool.upper()}</b>\n"
        f"👤 Аккаунт: <code>{acc_username}</code>\n"
        f"👥 Кол-во: <b>{acc_count}</b>\n"
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
    count   = session.get("acc_count", 1)
    price   = calc_price(pool, hours, count)
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
        "acc_count":    count,
        "rate":         price["rate"],
        "base":         price["base"],
        "discount":     price["discount"],
        "discount_pct": price["discount_pct"],
        "total":        price["total"],
        "owner_share":  price["owner_share"],
        "club_share":   price["club_share"],
        "recorded_at":  str(date.today()),
    }
    ledger[day_key]["sessions"].append(entry)
    ledger[day_key]["total"]       = round(sum(e["total"] for e in ledger[day_key]["sessions"]), 2)
    ledger[day_key]["owner_total"] = round(sum(e["owner_share"] for e in ledger[day_key]["sessions"]), 2)
    ledger[day_key]["club_total"]  = round(sum(e["club_share"] for e in ledger[day_key]["sessions"]), 2)
    _save_ledger(ledger)


def _salary_block(owner: float, club: float, total: float) -> str:
    return (
        f"\n{'─'*20}\n"
        f"💼 <b>Зарплата владельца: {owner} {CURRENCY}</b>\n"
        f"🏢 <b>Зарплата клуба: {club} {CURRENCY}</b>\n"
        f"💰 <b>Общая выручка: {total} {CURRENCY}</b>"
    )


def report_day(day: Optional[str] = None) -> str:
    day_key = day or date.today().isoformat()
    data    = _load_ledger().get(day_key)
    if not data or not data.get("sessions"):
        return f"📅 <b>{day_key}</b>\n\nСессий нет."
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
    for pn, st in by_pool.items():
        lines.append(f"🎮 {pn}: {st['count']} сес. × {round(st['hours'],1)} ч = <b>{round(st['total'],2)} {CURRENCY}</b>")
    lines.append(f"📊 Всего сессий: {len(sessions)}")
    lines.append(_salary_block(owner_total, club_total, total))
    return "\n".join(lines)


def report_period(days: int = 7) -> str:
    ledger = _load_ledger()
    today  = date.today()
    grand_total = grand_owner = grand_club = 0.0
    grand_cnt   = 0
    lines = [f"📊 <b>Отчёт за {days} дней</b>\n"]
    for i in range(days - 1, -1, -1):
        dk   = (today - timedelta(days=i)).isoformat()
        data = ledger.get(dk)
        if not data or not data.get("sessions"):
            lines.append(f"📅 {dk}: —"); continue
        dt = data["total"]
        do = data.get("owner_total", round(dt * OWNER_SHARE, 2))
        dc = data.get("club_total",  round(dt * CLUB_SHARE, 2))
        cnt = len(data["sessions"])
        grand_total += dt; grand_owner += do; grand_club += dc; grand_cnt += cnt
        lines.append(f"📅 {dk}: {cnt} сес. — <b>{dt} {CURRENCY}</b> (вл: {do} / клуб: {dc})")
    lines.append(f"📊 Всего: {grand_cnt}")
    lines.append(_salary_block(round(grand_owner,2), round(grand_club,2), round(grand_total,2)))
    return "\n".join(lines)


def reset_billing():
    if os.path.exists(LEDGER_FILE):
        os.remove(LEDGER_FILE)
    logger.info("Billing RU: сброшен")
