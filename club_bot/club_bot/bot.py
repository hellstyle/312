#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Бот для ПК клубов — весь функционал администратора на кнопках.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, date

import telebot
from telebot.types import (
    InlineKeyboardMarkup as IKM,
    InlineKeyboardButton as IKB,
    Message,
    CallbackQuery,
)

from club_bot.storage import load_clubs, save_clubs, load_sessions, save_sessions
from club_bot.accounts import load_pool, get_guard_code, POOLS
from club_bot import billing

logger = logging.getLogger("ClubBot")

# ---------------------------------------------------------------------------
# Callback константы
# ---------------------------------------------------------------------------
CB_GRANT_ADMIN  = "cg_adm"
CB_DENY         = "cg_deny"
CB_REVOKE       = "cg_rev"
CB_GUARD_AGAIN  = "cg_grd"
CB_LOGGED_IN    = "cg_log"
CB_EXTEND_YES   = "cg_ext_y"
CB_EXTEND_NO    = "cg_ext_n"
CB_END_SESSION  = "cg_end"

# Меню администратора
CB_MENU_MAIN    = "cm_main"
CB_MENU_ACCS    = "cm_accs"       # выбор пула
CB_POOL_ACCS    = "cm_pool"       # список аккаунтов пула
CB_ACC_ACTION   = "cm_acc"        # действия с аккаунтом
CB_GIVE_HOURS   = "cm_give_h"     # выбор часов
CB_QR_HOURS     = "cm_qr_h"       # выбор часов для QR
CB_BILLING_MENU = "cm_bill"       # меню бухгалтерии

OWNER_ID           = 6701153666
WARNING_BEFORE_END = 15 * 60
CHECK_INTERVAL     = 60
QR_ACTIVATION_TIMEOUT = 30 * 60  # 30 мин — если не нажал "Зашёл", сессия закрывается

# Пресеты часов для кнопок
HOUR_PRESETS = [1, 2, 3, 4, 6, 8, 12, 24]


class ClubBot:
    def __init__(self, token: str):
        self.bot = telebot.TeleBot(token, parse_mode="HTML", allow_sending_without_reply=True)
        self._bot_username: str | None = None
        self.clubs: dict    = load_clubs()
        self.sessions: dict = load_sessions()
        self._pending: dict = {}
        self._register_handlers()
        self._start_session_watcher()

    # ------------------------------------------------------------------
    # Утилиты
    # ------------------------------------------------------------------

    def _is_owner(self, uid: int) -> bool:
        return uid == OWNER_ID

    def _is_admin(self, uid: int) -> bool:
        club = self.clubs.get(str(uid))
        return bool(club and club.get("role") == "admin")

    def _is_authorized(self, uid: int) -> bool:
        return self._is_owner(uid) or self._is_admin(uid)

    def _get_club(self, uid: int) -> dict | None:
        return self.clubs.get(str(uid))

    def _save(self):
        save_clubs(self.clubs)

    def _save_sessions(self):
        save_sessions(self.sessions)

    def _notify_owner(self, text: str, kb=None):
        try:
            self.bot.send_message(OWNER_ID, text, reply_markup=kb)
        except Exception as e:
            logger.error(f"_notify_owner: {e}")

    def _fmt_pools(self, pools: list[str]) -> str:
        return ", ".join(p.upper() for p in pools) if pools else "нет"

    def _get_bot_username(self) -> str:
        if not self._bot_username:
            try:
                self._bot_username = self.bot.get_me().username
            except Exception:
                self._bot_username = "unknown"
        return self._bot_username

    def _get_allowed_pools(self, uid: int) -> list[str]:
        if self._is_owner(uid):
            return list(POOLS.keys())
        club = self._get_club(uid)
        return club.get("pools", []) if club else []

    def _get_active_session_for_acc(self, pool: str, acc_idx: int) -> dict | None:
        for s in self.sessions.values():
            if s.get("active") and s.get("pool") == pool and s.get("acc_idx") == acc_idx:
                return s
        return None

    def _acc_status_line(self, pool: str, idx: int) -> str:
        s = self._get_active_session_for_acc(pool, idx)
        if not s:
            return "🟢"
        ends_at = s.get("ends_at")
        if ends_at:
            remaining = max(0, int(ends_at - time.time()))
            h, m = remaining // 3600, (remaining % 3600) // 60
            return f"🔴 {h}ч{m}м"
        return "🟡"

    def _account_text(self, acc: dict, pool: str, idx: int,
                      session_id: str | None = None) -> tuple[str, IKM]:
        code, remaining = get_guard_code(acc["shared_secret"])
        text = (
            f"🎮 <b>{pool.upper()} аккаунт #{idx}</b>\n\n"
            f"👤 Логин: <code>{acc['username']}</code>\n"
            f"🔑 Пароль: <code>{acc['password']}</code>\n"
            f"🛡 Guard: <code>{code or '—'}</code>"
            + (f"  <i>(ещё {remaining} сек)</i>" if code else "") +
            "\n\n⚠️ <b>Передача данных аккаунта третьим лицам строго запрещена!</b>"
        )
        kb = IKM()
        if session_id:
            kb.row(
                IKB("🔄 Guard снова",      callback_data=f"{CB_GUARD_AGAIN}:{session_id}"),
                IKB("✅ Зашёл на аккаунт", callback_data=f"{CB_LOGGED_IN}:{session_id}"),
            )
        return text, kb

    # ------------------------------------------------------------------
    # Клавиатуры меню
    # ------------------------------------------------------------------

    def _kb_main_menu(self, uid: int) -> IKM:
        """Главное меню администратора."""
        kb = IKM()
        kb.row(IKB("🎮 Аккаунты",        callback_data=CB_MENU_ACCS))
        kb.row(IKB("📋 Активные сессии", callback_data=f"{CB_MENU_MAIN}:sessions"))
        kb.row(IKB("💰 Бухгалтерия",     callback_data=CB_BILLING_MENU))
        if self._is_owner(uid):
            kb.row(IKB("👥 Клубы",        callback_data=f"{CB_MENU_MAIN}:clubs"))
        return kb

    def _kb_pool_select(self, action: str, uid: int) -> IKM:
        """Выбор пула — две большие кнопки."""
        kb    = IKM()
        pools = self._get_allowed_pools(uid)
        for p in pools:
            accs   = load_pool(POOLS[p])
            free   = sum(1 for i in range(1, len(accs)+1)
                         if not self._get_active_session_for_acc(p, i))
            total  = len(accs)
            kb.row(IKB(f"🎮 {p.upper()}  —  {free}/{total} свободно",
                       callback_data=f"{CB_POOL_ACCS}:{action}:{p}"))
        kb.row(IKB("◀️ Назад", callback_data=CB_MENU_MAIN + ":back"))
        return kb

    def _kb_accounts(self, pool: str, action: str, uid: int) -> IKM:
        """Список аккаунтов пула с статусом."""
        kb   = IKM()
        accs = load_pool(POOLS[pool])
        row  = []
        for i, acc in enumerate(accs, 1):
            status = self._acc_status_line(pool, i)
            btn    = IKB(f"#{i} {acc['username'][:10]} {status}",
                         callback_data=f"{CB_ACC_ACTION}:{action}:{pool}:{i}")
            row.append(btn)
            if len(row) == 2:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)
        kb.row(IKB("◀️ Назад", callback_data=f"{CB_POOL_ACCS}:back:{pool}"))
        return kb

    def _kb_hours(self, cb_prefix: str, pool: str, acc_idx: int) -> IKM:
        """Выбор количества часов — зачёркнутая базовая цена и финальная рядом."""
        kb  = IKM()
        row = []
        for h in HOUR_PRESETS:
            price = billing.calc_price(pool, h)
            if price["discount_pct"]:
                label = f"{h}ч  {int(price['base'])}→{int(price['total'])}₽"
            else:
                label = f"{h}ч  —  {int(price['total'])}₽"
            row.append(IKB(label, callback_data=f"{cb_prefix}:{pool}:{acc_idx}:{h}"))
            if len(row) == 2:
                kb.row(*row)
                row = []
        if row:
            kb.row(*row)
        kb.row(IKB("◀️ Назад", callback_data=f"{CB_ACC_ACTION}:back:{pool}:{acc_idx}"))
        return kb

    def _kb_acc_actions(self, pool: str, acc_idx: int) -> IKM:
        """Действия с конкретным аккаунтом."""
        kb     = IKM()
        active = self._get_active_session_for_acc(pool, acc_idx)
        accs   = load_pool(POOLS[pool])
        acc    = accs[acc_idx - 1] if acc_idx <= len(accs) else None
        acc_name = acc["username"] if acc else f"#{acc_idx}"

        if active:
            ends_at = active.get("ends_at")
            if ends_at:
                remaining = max(0, int(ends_at - time.time()))
                h, m = remaining // 3600, (remaining % 3600) // 60
                status = f"🔴 Занят — {h}ч {m}м"
            else:
                status = "🟡 Занят (таймер не запущен)"
            kb.row(IKB(f"🛑 Завершить сессию",
                       callback_data=f"{CB_END_SESSION}:{active['session_id']}"))
        else:
            status = "🟢 Свободен"

        kb.row(
            IKB("📱 Выдать (QR)", callback_data=f"{CB_QR_HOURS}:sel:{pool}:{acc_idx}"),
        )
        kb.row(IKB("◀️ Назад", callback_data=f"{CB_POOL_ACCS}:give:{pool}"))
        return kb, status, acc_name

    def _kb_billing(self) -> IKM:
        """Меню бухгалтерии."""
        kb = IKM()
        kb.row(IKB("📅 Сегодня",   callback_data=f"{CB_BILLING_MENU}:day"))
        kb.row(
            IKB("📆 7 дней",  callback_data=f"{CB_BILLING_MENU}:7"),
            IKB("📆 30 дней", callback_data=f"{CB_BILLING_MENU}:30"),
        )
        kb.row(IKB("📋 Детально (сегодня)", callback_data=f"{CB_BILLING_MENU}:detail"))
        kb.row(
            IKB("📊 Excel сегодня",  callback_data=f"{CB_BILLING_MENU}:excel_day"),
            IKB("📊 Excel 7 дней",   callback_data=f"{CB_BILLING_MENU}:excel_7"),
        )
        kb.row(IKB("📊 Excel 30 дней", callback_data=f"{CB_BILLING_MENU}:excel_30"))
        kb.row(IKB("◀️ Назад", callback_data=CB_MENU_MAIN + ":back"))
        return kb

    def _kb_clubs(self) -> IKM:
        """Список клубов с кнопками отзыва."""
        kb = IKM()
        for uid_str, info in self.clubs.items():
            name = f"{info.get('name','?')} (@{info.get('username','?')})"
            kb.row(IKB(f"🚫 {name}", callback_data=f"{CB_REVOKE}:{uid_str}"))
        kb.row(IKB("◀️ Назад", callback_data=CB_MENU_MAIN + ":back"))
        return kb

    # ------------------------------------------------------------------
    # Хендлеры
    # ------------------------------------------------------------------

    def _register_handlers(self):
        bot = self.bot

        @bot.message_handler(commands=["start"])
        def on_start(m: Message):
            uid  = m.from_user.id
            args = m.text.strip().split(maxsplit=1)
            arg  = args[1].strip() if len(args) > 1 else ""

            # QR deep link
            if arg.startswith("qr_"):
                self._handle_qr_token(m, arg[3:])
                return

            if self._is_owner(uid):
                bot.send_message(m.chat.id,
                    "👑 <b>Панель владельца</b>\n\nВыберите раздел:",
                    reply_markup=self._kb_main_menu(uid))
                return

            if self._is_admin(uid):
                club  = self._get_club(uid)
                pools = self._fmt_pools(club.get("pools", []))
                bot.send_message(m.chat.id,
                    f"👨‍💼 <b>Панель администратора</b>\n"
                    f"🎮 Пулы: <b>{pools}</b>\n\nВыберите раздел:",
                    reply_markup=self._kb_main_menu(uid))
                return

            # Проверяем игрока с активной сессией
            active_s = [s for s in self.sessions.values()
                        if s.get("active") and str(s.get("player_id")) == str(uid)]
            if active_s:
                s = active_s[0]
                ends_at = s.get("ends_at")
                if ends_at:
                    remaining = max(0, int(ends_at - time.time()))
                    h, mn = remaining // 3600, (remaining % 3600) // 60
                    bot.send_message(m.chat.id,
                        f"🎮 У вас активная сессия.\n"
                        f"⏱ Осталось: {h}ч {mn}м\n\n"
                        f"/extend [часы] — запросить продление")
                return

            # Новый пользователь
            name     = m.from_user.first_name or ""
            username = m.from_user.username or str(uid)
            self._pending[uid] = {"name": name, "username": username}
            bot.send_message(m.chat.id,
                "👋 Добро пожаловать!\n\nВаш запрос отправлен владельцу.\nОжидайте.")
            kb = IKM()
            kb.row(
                IKB("✅ Выдать права Админа", callback_data=f"{CB_GRANT_ADMIN}:{uid}"),
                IKB("❌ Отказать",            callback_data=f"{CB_DENY}:{uid}"),
            )
            self._notify_owner(
                f"🔔 Новый запрос доступа\n\n"
                f"👤 {name} (@{username})\n"
                f"🆔 ID: <code>{uid}</code>", kb)

        @bot.message_handler(commands=["menu"])
        def on_menu(m: Message):
            uid = m.from_user.id
            if not self._is_authorized(uid):
                return
            bot.send_message(m.chat.id, "📋 Главное меню:",
                             reply_markup=self._kb_main_menu(uid))

        # /status — статус сессии для игрока
        @bot.message_handler(commands=["status"])
        def on_status(m: Message):
            uid = str(m.from_user.id)
            active = [(sid, s) for sid, s in self.sessions.items()
                      if s.get("active") and str(s.get("player_id")) == uid]
            if not active:
                bot.send_message(m.chat.id, "❌ У вас нет активной сессии.")
                return
            sid, s = active[0]
            ends_at    = s.get("ends_at")
            started_at = s.get("started_at")
            if not started_at:
                bot.send_message(m.chat.id,
                    f"🎮 <b>{s['pool'].upper()} #{s['acc_idx']}</b>\n\n"
                    f"⏳ Таймер ещё не запущен.\n"
                    f"Нажмите <b>✅ Зашёл на аккаунт</b> чтобы начать сессию.")
                return
            remaining = max(0, int(ends_at - time.time()))
            h  = remaining // 3600
            mn = (remaining % 3600) // 60
            sc = remaining % 60
            ends_str = datetime.fromtimestamp(ends_at).strftime("%H:%M")
            bot.send_message(m.chat.id,
                f"🎮 <b>{s['pool'].upper()} #{s['acc_idx']}</b>\n\n"
                f"⏱ Осталось: <b>{h}ч {mn}м {sc}с</b>\n"
                f"🕐 Сессия до: <b>{ends_str}</b>\n\n"
                f"/extend [часы] — запросить продление")

        # /extend — запрос продления от игрока
        @bot.message_handler(commands=["extend"])
        def on_extend(m: Message):
            uid = str(m.from_user.id)
            active_sessions = [
                (sid, s) for sid, s in self.sessions.items()
                if s.get("active") and str(s.get("player_id")) == uid
            ]
            if not active_sessions:
                bot.send_message(m.chat.id, "❌ У вас нет активной сессии.")
                return
            parts = m.text.strip().split()
            try:
                extra_hours = float(parts[1]) if len(parts) > 1 else 1.0
            except ValueError:
                extra_hours = 1.0
            sid, s = active_sessions[0]
            kb = IKM()
            kb.row(
                IKB(f"✅ Продлить {extra_hours} ч",
                    callback_data=f"{CB_EXTEND_YES}:{sid}:{extra_hours}"),
                IKB("❌ Отклонить", callback_data=f"{CB_EXTEND_NO}:{sid}"),
            )
            self._notify_owner(
                f"⏳ Запрос на продление\n\n"
                f"👤 Игрок: <code>{uid}</code>\n"
                f"🎯 {s['pool'].upper()} #{s['acc_idx']} ({s['acc_username']})\n"
                f"➕ {extra_hours} ч", kb)
            bot.send_message(m.chat.id,
                f"✅ Запрос на продление {extra_hours} ч отправлен.")

        # /price — калькулятор
        @bot.message_handler(commands=["price"])
        def on_price(m: Message):
            if not self._is_authorized(m.from_user.id):
                return
            parts = m.text.strip().split()
            if len(parts) < 3:
                bot.send_message(m.chat.id, "Использование: /price [rust|cs] [часы]")
                return
            try:
                pool  = parts[1].lower()
                hours = float(parts[2])
            except ValueError:
                bot.send_message(m.chat.id, "❌ Неверный формат.")
                return
            bot.send_message(m.chat.id, billing.format_receipt(pool, hours, "—"))

        # Главный callback обработчик
        @bot.callback_query_handler(func=lambda c: True)
        def on_callback(c: CallbackQuery):
            data = c.data
            uid  = c.from_user.id
            cid  = c.message.chat.id
            mid  = c.message.id
            bot.answer_callback_query(c.id)

            # ── Главное меню (назад) ─────────────────────────────────────
            if data in (CB_MENU_MAIN + ":back", CB_MENU_MAIN + ":sessions",
                        CB_MENU_MAIN + ":clubs"):
                if not self._is_authorized(uid):
                    return

                if data == CB_MENU_MAIN + ":back":
                    bot.edit_message_text("📋 Главное меню:", cid, mid,
                                          reply_markup=self._kb_main_menu(uid))

                elif data == CB_MENU_MAIN + ":sessions":
                    active = {k: v for k, v in self.sessions.items() if v.get("active")}
                    if not active:
                        bot.edit_message_text("📋 Нет активных сессий.", cid, mid,
                                              reply_markup=self._kb_main_menu(uid))
                        return
                    bot.edit_message_text("📋 Активные сессии:", cid, mid,
                                          reply_markup=self._kb_main_menu(uid))
                    for sid, s in active.items():
                        ends_at  = s.get("ends_at")
                        time_str = (
                            f"{max(0,int(ends_at-time.time()))//3600}ч "
                            f"{(max(0,int(ends_at-time.time()))%3600)//60}м"
                            if ends_at else "таймер не запущен"
                        )
                        kb2 = IKM()
                        kb2.row(IKB("🛑 Завершить", callback_data=f"{CB_END_SESSION}:{sid}"))
                        bot.send_message(cid,
                            f"🎮 {s['pool'].upper()} #{s['acc_idx']} ({s['acc_username']})\n"
                            f"👤 Игрок: <code>{s['player_id']}</code>\n"
                            f"⏱ Осталось: {time_str}",
                            reply_markup=kb2)

                elif data == CB_MENU_MAIN + ":clubs":
                    if not self._is_owner(uid):
                        return
                    if not self.clubs:
                        bot.edit_message_text("👥 Нет зарегистрированных клубов.", cid, mid,
                                              reply_markup=self._kb_main_menu(uid))
                        return
                    lines = ["👥 <b>Клубы:</b>\n"]
                    for uid_str, info in self.clubs.items():
                        role  = "Админ" if info.get("role") == "admin" else "Игрок"
                        pools = self._fmt_pools(info.get("pools", []))
                        lines.append(
                            f"• {info.get('name','?')} (@{info.get('username','?')})\n"
                            f"  Роль: {role} | Пулы: {pools} | ID: <code>{uid_str}</code>")
                    bot.edit_message_text("\n".join(lines), cid, mid,
                                          reply_markup=self._kb_clubs())

            # ── Выбор пула ──────────────────────────────────────────────
            elif data == CB_MENU_ACCS:
                if not self._is_authorized(uid): return
                bot.edit_message_text("🎮 Выберите пул:", cid, mid,
                                      reply_markup=self._kb_pool_select("give", uid))

            # ── Список аккаунтов пула ────────────────────────────────────
            elif data.startswith(f"{CB_POOL_ACCS}:"):
                if not self._is_authorized(uid): return
                _, action, pool = data.split(":", 2)
                if action == "back":
                    bot.edit_message_text("🎮 Выберите пул:", cid, mid,
                                          reply_markup=self._kb_pool_select("give", uid))
                    return
                bot.edit_message_text(f"🎮 <b>{pool.upper()} аккаунты</b>\n\n"
                                      f"🟢 свободен  🔴 занят  🟡 нет таймера",
                                      cid, mid,
                                      reply_markup=self._kb_accounts(pool, action, uid))

            # ── Действия с аккаунтом ─────────────────────────────────────
            elif data.startswith(f"{CB_ACC_ACTION}:"):
                if not self._is_authorized(uid): return
                parts    = data.split(":")
                action   = parts[1]
                pool     = parts[2]
                acc_idx  = int(parts[3])

                if action == "back":
                    bot.edit_message_text(f"🎮 Выберите пул:", cid, mid,
                                          reply_markup=self._kb_pool_select("give", uid))
                    return

                kb_acc, status, acc_name = self._kb_acc_actions(pool, acc_idx)
                accs = load_pool(POOLS[pool])
                acc  = accs[acc_idx - 1] if acc_idx <= len(accs) else None
                price_1h = billing.calc_price(pool, 1)

                bot.edit_message_text(
                    f"🎮 <b>{pool.upper()} #{acc_idx}</b> — {acc_name}\n"
                    f"📊 Статус: {status}\n"
                    f"💰 Ставка: {price_1h['rate']} ₽/ч\n\n"
                    f"Выберите действие:",
                    cid, mid, reply_markup=kb_acc)

            # ── Выбор часов для QR ──────────────────────────────────────
            elif data.startswith(f"{CB_QR_HOURS}:"):
                if not self._is_authorized(uid): return
                parts   = data.split(":")
                action  = parts[1]
                pool    = parts[2]
                acc_idx = int(parts[3])

                if action == "sel":
                    bot.edit_message_text(
                        f"⏱ Выберите время аренды для {pool.upper()} #{acc_idx}:\n\n"
                        f"⚠️ <b>Выдавайте аккаунт только после получения оплаты!</b>",
                        cid, mid,
                        reply_markup=self._kb_hours(CB_QR_HOURS + ":do", pool, acc_idx))
                    return

                # action == "do" — генерируем QR
                hours = float(parts[4])
                accs  = load_pool(POOLS[pool])
                if acc_idx < 1 or acc_idx > len(accs):
                    bot.send_message(cid, "❌ Аккаунт не найден.")
                    return
                acc = accs[acc_idx - 1]

                # Жёсткая проверка занятости — блокируем если аккаунт занят
                active = self._get_active_session_for_acc(pool, acc_idx)
                if active:
                    ends_at = active.get("ends_at")
                    started = active.get("started_at")
                    if ends_at and started:
                        remaining = max(0, int(ends_at - time.time()))
                        h2, mn2   = remaining // 3600, (remaining % 3600) // 60
                        ends_str  = datetime.fromtimestamp(ends_at).strftime("%H:%M")
                        bot.edit_message_text(
                            f"🔴 <b>Аккаунт занят!</b>\n\n"
                            f"🎮 {pool.upper()} #{acc_idx} — {acc['username']}\n"
                            f"👤 Игрок: <code>{active['player_id']}</code>\n"
                            f"⏱ Освободится через <b>{h2}ч {mn2}м</b> (в {ends_str})\n\n"
                            f"Выберите другой аккаунт или дождитесь освобождения.",
                            cid, mid,
                            reply_markup=self._kb_accounts(pool, "give", uid))
                    elif not started:
                        # Выдан QR но игрок ещё не зашёл
                        bot.edit_message_text(
                            f"🟡 <b>Аккаунт зарезервирован!</b>\n\n"
                            f"🎮 {pool.upper()} #{acc_idx} — {acc['username']}\n"
                            f"👤 Игрок: <code>{active['player_id']}</code>\n"
                            f"⏳ QR выдан, игрок ещё не зашёл на аккаунт.\n\n"
                            f"Выберите другой аккаунт.",
                            cid, mid,
                            reply_markup=self._kb_accounts(pool, "give", uid))
                    return

                try:
                    from club_bot.qr_generator import create_qr_token, generate_qr_image
                    token  = create_qr_token(pool, acc_idx, hours, uid)
                    qr_img = generate_qr_image(self._get_bot_username(), token)
                    bot.send_photo(cid, qr_img,
                        caption=(
                            f"📱 <b>QR — {pool.upper()} #{acc_idx}</b>\n"
                            f"👤 {acc['username']}\n"
                            f"⏱ {hours} ч | {billing.calc_price(pool,hours)['total']} ₽\n\n"
                            f"📲 <b>Игрок сканирует QR → открывается Telegram бот → "
                            f"автоматически выдаются данные аккаунта</b>\n"
                            f"⏳ Действителен 24 часа\n\n"
                            f"⚠️ Передача данных третьим лицам запрещена!"
                        ))
                    # Возврат в главное меню
                    bot.edit_message_text(
                        f"✅ QR выдан для {pool.upper()} #{acc_idx} на {hours} ч\n\n"
                        f"📋 Главное меню:",
                        cid, mid,
                        reply_markup=self._kb_main_menu(uid))
                except ImportError:
                    bot.send_message(cid, "❌ pip install qrcode[pil]")
                except Exception as e:
                    bot.send_message(cid, f"❌ Ошибка QR: {e}")

            # ── Бухгалтерия ──────────────────────────────────────────────
            elif data.startswith(f"{CB_BILLING_MENU}"):
                if not self._is_authorized(uid): return
                sub = data.split(":", 1)[1] if ":" in data else ""

                if not sub:
                    bot.edit_message_text("💰 <b>Бухгалтерия</b>\n\nВыберите период:",
                                          cid, mid, reply_markup=self._kb_billing())
                elif sub == "day":
                    bot.edit_message_text(billing.report_day(), cid, mid,
                                          reply_markup=self._kb_billing())
                elif sub == "7":
                    bot.edit_message_text(billing.report_period(7), cid, mid,
                                          reply_markup=self._kb_billing())
                elif sub == "30":
                    bot.edit_message_text(billing.report_period(30), cid, mid,
                                          reply_markup=self._kb_billing())
                elif sub == "detail":
                    bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                    for msg in billing.report_detailed_day():
                        bot.send_message(cid, msg)
                    bot.send_message(cid, "📊 Детальный отчёт выше.",
                                     reply_markup=self._kb_billing())

                elif sub.startswith("excel_"):
                    from club_bot.excel_export import export_day, export_period
                    try:
                        if sub == "excel_day":
                            buf      = export_day()
                            filename = f"report_{date.today().isoformat()}.xlsx"
                            caption  = f"📊 Excel отчёт за {date.today().isoformat()}"
                        elif sub == "excel_7":
                            buf      = export_period(7)
                            filename = f"report_7days_{date.today().isoformat()}.xlsx"
                            caption  = "📊 Excel отчёт за 7 дней"
                        else:
                            buf      = export_period(30)
                            filename = f"report_30days_{date.today().isoformat()}.xlsx"
                            caption  = "📊 Excel отчёт за 30 дней"
                        buf.name = filename
                        bot.send_document(cid, buf, caption=caption)
                    except ImportError:
                        bot.send_message(cid, "❌ pip install openpyxl")
                    except Exception as e:
                        bot.send_message(cid, f"❌ Ошибка генерации Excel: {e}")

            # ── Выдача прав ──────────────────────────────────────────────
            elif data.startswith(f"{CB_GRANT_ADMIN}:"):
                if not self._is_owner(uid): return
                target_id = int(data.split(":")[1])
                req = self._pending.pop(target_id, {})
                self.clubs[str(target_id)] = {
                    "name":       req.get("name", "?"),
                    "username":   req.get("username", "?"),
                    "role":       "admin",
                    "pools":      list(POOLS.keys()),
                    "granted_at": datetime.now().isoformat(),
                }
                self._save()
                bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                bot.send_message(cid, f"✅ Права Администратора выданы <code>{target_id}</code>.")
                try:
                    bot.send_message(target_id,
                        "✅ Ваш запрос одобрен!\n\n"
                        "👨‍💼 Роль: <b>Администратор клуба</b>\n"
                        f"🎮 Пулы: <b>{self._fmt_pools(list(POOLS.keys()))}</b>\n\n"
                        "Нажмите /menu для открытия панели управления.")
                except Exception:
                    pass

            elif data.startswith(f"{CB_DENY}:"):
                if not self._is_owner(uid): return
                target_id = int(data.split(":")[1])
                self._pending.pop(target_id, None)
                bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                bot.send_message(cid, f"❌ Отказано <code>{target_id}</code>.")
                try:
                    bot.send_message(target_id, "❌ В доступе отказано.")
                except Exception:
                    pass

            elif data.startswith(f"{CB_REVOKE}:"):
                if not self._is_owner(uid): return
                tid = data.split(":")[1]
                self.clubs.pop(tid, None)
                self._save()
                bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                bot.send_message(cid, f"✅ Доступ отозван у <code>{tid}</code>.")
                try:
                    bot.send_message(int(tid), "🚫 Ваш доступ был отозван.")
                except Exception:
                    pass

            elif data.startswith(f"{CB_GUARD_AGAIN}:"):
                sid = data.split(":", 1)[1]
                s   = self.sessions.get(sid)
                if not s or not s.get("active"):
                    bot.answer_callback_query(c.id, "❌ Сессия завершена. Доступ закрыт.", show_alert=True)
                    # Убираем кнопки с устаревшего сообщения
                    try:
                        bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                    except Exception:
                        pass
                    return
                code, remaining = get_guard_code(s["acc_shared_secret"])
                bot.send_message(cid,
                    f"🛡 Guard: <code>{code or '—'}</code>"
                    + (f"  <i>(ещё {remaining} сек)</i>" if code else ""))

            elif data.startswith(f"{CB_LOGGED_IN}:"):
                sid = data.split(":", 1)[1]
                s   = self.sessions.get(sid)
                if not s or not s.get("active"):
                    bot.answer_callback_query(c.id, "❌ Сессия завершена.", show_alert=True)
                    try:
                        bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                    except Exception:
                        pass
                    return
                if s.get("started_at"):
                    bot.answer_callback_query(c.id, "ℹ️ Таймер уже запущен.")
                    return
                now           = time.time()
                s["started_at"] = now
                s["ends_at"]    = now + s["hours"] * 3600
                s["warned"]     = False
                self._save_sessions()
                # Останавливаем автообновление Guard
                self._stop_guard_autoupdate(sid)
                bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                ends_str = datetime.fromtimestamp(s["ends_at"]).strftime("%H:%M")
                bot.send_message(cid,
                    f"✅ Таймер запущен! До <b>{ends_str}</b> ({s['hours']} ч)\n\n"
                    f"⚠️ Передача данных третьим лицам запрещена!\n"
                    f"/extend [часы] — запросить продление")
                self._notify_owner(
                    f"▶️ Сессия запущена\n\n"
                    f"👤 <code>{s['player_id']}</code>\n"
                    f"🎯 {s['pool'].upper()} #{s['acc_idx']} ({s['acc_username']})\n"
                    f"⏱ До {ends_str}")

            elif data.startswith(f"{CB_EXTEND_YES}:"):
                if not self._is_owner(uid): return
                _, sid, eh = data.split(":")
                s = self.sessions.get(sid)
                if not s or not s.get("active"): return
                s["ends_at"] += float(eh) * 3600
                s["warned"]   = False
                self._save_sessions()
                bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                ends_str = datetime.fromtimestamp(s["ends_at"]).strftime("%H:%M")
                bot.send_message(cid, f"✅ Продлено на {eh} ч. До {ends_str}")
                try:
                    bot.send_message(s["player_id"],
                        f"✅ Сессия продлена на {eh} ч. До <b>{ends_str}</b>")
                except Exception:
                    pass

            elif data.startswith(f"{CB_EXTEND_NO}:"):
                if not self._is_owner(uid): return
                sid = data.split(":")[1]
                bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                bot.send_message(cid, "❌ Продление отклонено.")
                s = self.sessions.get(sid)
                if s:
                    try:
                        bot.send_message(s["player_id"], "❌ Продление отклонено.")
                    except Exception:
                        pass

            elif data.startswith(f"{CB_END_SESSION}:"):
                if not self._is_authorized(uid): return
                sid = data.split(":", 1)[1]
                self._end_session(sid, "manual")
                bot.edit_message_text(
                    "✅ Сессия завершена.\n\n📋 Главное меню:",
                    cid, mid,
                    reply_markup=self._kb_main_menu(uid))

        # Игнорирование сообщений от игроков без активной сессии
        @bot.message_handler(func=lambda m: True, content_types=["text", "photo", "sticker", "document"])
        def on_any_message(m: Message):
            uid = m.from_user.id
            if self._is_authorized(uid):
                return  # админы и владелец — не трогаем
            # Проверяем — есть ли активная сессия
            has_active = any(
                s.get("active") and str(s.get("player_id")) == str(uid)
                for s in self.sessions.values()
            )
            if has_active:
                return  # у игрока есть сессия — не мешаем
            # Проверяем — была ли завершённая сессия
            had_session = any(
                not s.get("active") and str(s.get("player_id")) == str(uid)
                for s in self.sessions.values()
            )
            if had_session:
                # Молча игнорируем — не отвечаем вообще
                return
    # ------------------------------------------------------------------

    def _handle_qr_token(self, m: Message, token: str):
        from club_bot.qr_generator import get_qr_token, mark_token_used
        data = get_qr_token(token)
        if not data:
            self.bot.send_message(m.chat.id,
                "❌ QR код недействителен или уже использован.\n"
                "Запросите новый у администратора.")
            return
        pool     = data["pool"]
        acc_idx  = data["acc_idx"]
        hours    = data["hours"]
        admin_id = data["admin_id"]
        uid      = m.from_user.id
        accs     = load_pool(POOLS[pool])
        if acc_idx < 1 or acc_idx > len(accs):
            self.bot.send_message(m.chat.id, "❌ Аккаунт не найден.")
            return
        acc = accs[acc_idx - 1]
        mark_token_used(token)
        active = self._get_active_session_for_acc(pool, acc_idx)
        if active:
            ends_at = active.get("ends_at")
            if ends_at:
                remaining = max(0, int(ends_at - time.time()))
                h, mn = remaining // 3600, (remaining % 3600) // 60
                self._notify_owner(
                    f"⚠️ QR на занятый аккаунт!\n\n"
                    f"👤 Игрок: <code>{uid}</code>\n"
                    f"🎯 {pool.upper()} #{acc_idx} ({acc['username']})\n"
                    f"Занят ещё {h}ч {mn}м")
        session_id = f"{uid}_{pool}_{acc_idx}_{int(time.time())}"
        self.sessions[session_id] = {
            "session_id":        session_id,
            "player_id":         uid,
            "admin_id":          admin_id,
            "pool":              pool,
            "acc_idx":           acc_idx,
            "acc_username":      acc["username"],
            "acc_password":      acc["password"],
            "acc_shared_secret": acc["shared_secret"],
            "hours":             hours,
            "started_at":        None,
            "ends_at":           None,
            "warned":            False,
            "active":            True,
            "created_at":        time.time(),
        }
        self._save_sessions()
        text, kb = self._account_text(acc, pool, acc_idx, session_id)
        self.bot.send_message(m.chat.id, text, reply_markup=kb)
        # Запускаем автообновление Guard кода пока не нажато "Зашёл"
        self._start_guard_autoupdate(uid, session_id, acc["shared_secret"])
        self._notify_owner(
            f"📱 QR активирован\n\n"
            f"👤 <code>{uid}</code>\n"
            f"🎯 {pool.upper()} #{acc_idx} ({acc['username']})\n"
            f"⏱ {hours} ч")

    # ------------------------------------------------------------------
    # Автообновление Guard кода до входа на аккаунт
    # ------------------------------------------------------------------

    def _start_guard_autoupdate(self, player_id: int, session_id: str, shared_secret: str):
        """Каждые 25 сек отправляет новый Guard код пока не нажато 'Зашёл'."""
        stop_event = threading.Event()
        # Храним события по session_id чтобы можно было остановить
        if not hasattr(self, "_guard_stops"):
            self._guard_stops = {}
        self._guard_stops[session_id] = stop_event

        def _loop():
            time.sleep(25)  # первое обновление через 25 сек после получения
            while not stop_event.is_set():
                s = self.sessions.get(session_id)
                # Если сессия завершена или таймер уже запущен — стоп
                if not s or not s.get("active") or s.get("started_at"):
                    break
                code, remaining = get_guard_code(shared_secret)
                if code:
                    try:
                        self.bot.send_message(
                            player_id,
                            f"🔄 <b>Обновлённый Guard код:</b>\n"
                            f"<code>{code}</code>  <i>(ещё {remaining} сек)</i>"
                        )
                    except Exception as e:
                        logger.error(f"guard_autoupdate: {e}")
                        break
                stop_event.wait(timeout=25)

        threading.Thread(target=_loop, name=f"GuardUpdate_{session_id}", daemon=True).start()

    def _stop_guard_autoupdate(self, session_id: str):
        """Останавливает автообновление Guard кода."""
        if hasattr(self, "_guard_stops"):
            ev = self._guard_stops.pop(session_id, None)
            if ev:
                ev.set()

    # ------------------------------------------------------------------
    # Завершение сессии
    # ------------------------------------------------------------------

    def _end_session(self, session_id: str, reason: str = "expired"):
        s = self.sessions.get(session_id)
        if not s or not s.get("active"):
            return
        s["active"] = False
        s["ended_at"] = time.time()
        self._save_sessions()
        try:
            billing.record_session(s)
        except Exception as e:
            logger.error(f"billing.record_session: {e}")
        player_id = s["player_id"]
        pool      = s["pool"].upper()
        acc       = s["acc_username"]
        receipt   = billing.format_receipt(s["pool"], s["hours"], acc)
        if reason == "expired":
            try:
                self.bot.send_message(player_id,
                    f"⏰ Ваша сессия завершена.\n🎮 {pool} ({acc})\n\n{receipt}\n\n"
                    f"🔒 Доступ к аккаунту закрыт. Для продолжения обратитесь к администратору.")
            except Exception:
                pass
            self._notify_owner(
                f"⏰ Сессия истекла\n\n"
                f"👤 <code>{player_id}</code>\n"
                f"🎯 {pool} #{s['acc_idx']} ({acc})\n\n{receipt}")
        elif reason == "not_activated":
            try:
                self.bot.send_message(player_id,
                    f"⏰ Сессия отменена — вы не активировали аккаунт в течение 30 минут.\n"
                    f"🔒 Для получения доступа обратитесь к администратору.")
            except Exception:
                pass
            self._notify_owner(
                f"⚠️ Сессия не активирована и отменена\n\n"
                f"👤 <code>{player_id}</code>\n"
                f"🎯 {pool} #{s['acc_idx']} ({acc})\n"
                f"QR не был использован в течение 30 мин")
        else:
            try:
                self.bot.send_message(player_id,
                    f"🛑 Сессия завершена.\n\n{receipt}\n\n"
                    f"🔒 Доступ к аккаунту закрыт.")
            except Exception:
                pass
            self._notify_owner(
                f"🛑 Сессия завершена\n\n"
                f"👤 <code>{player_id}</code>\n"
                f"🎯 {pool} #{s['acc_idx']} ({acc})\n\n{receipt}")

    # ------------------------------------------------------------------
    # Наблюдатель сессий
    # ------------------------------------------------------------------

    def _start_session_watcher(self):
        def watch():
            while True:
                try:
                    now = time.time()
                    for sid, s in list(self.sessions.items()):
                        if not s.get("active"):
                            continue

                        ends_at    = s.get("ends_at")
                        started_at = s.get("started_at")
                        created_at = s.get("created_at", now)

                        # Таймер запущен — проверяем окончание
                        if ends_at:
                            if not s.get("warned") and 0 < ends_at - now <= WARNING_BEFORE_END:
                                s["warned"] = True
                                self._save_sessions()
                                remaining_min = int((ends_at - now) / 60)
                                try:
                                    self.bot.send_message(s["player_id"],
                                        f"⚠️ До конца сессии <b>{remaining_min} мин</b>!\n"
                                        f"/extend [часы] — запросить продление.")
                                except Exception:
                                    pass
                                self._notify_owner(
                                    f"⚠️ Сессия заканчивается через {remaining_min} мин\n\n"
                                    f"👤 <code>{s['player_id']}</code>\n"
                                    f"🎯 {s['pool'].upper()} #{s['acc_idx']} ({s['acc_username']})")
                            if now >= ends_at:
                                self._end_session(sid, "expired")

                        # Таймер НЕ запущен — если прошло более 30 мин с момента создания
                        elif not started_at:
                            if now - created_at > QR_ACTIVATION_TIMEOUT:
                                logger.info(f"Сессия {sid} не активирована за {QR_ACTIVATION_TIMEOUT//60} мин — автозавершение")
                                self._end_session(sid, "not_activated")

                except Exception as e:
                    logger.error(f"session_watcher: {e}")
                time.sleep(CHECK_INTERVAL)

        threading.Thread(target=watch, name="ClubBotWatcher", daemon=True).start()

    # ------------------------------------------------------------------
    # Запуск
    # ------------------------------------------------------------------

    def run(self):
        logger.info("ClubBot запущен")
        k = 0
        while True:
            try:
                self.bot.infinity_polling(
                    timeout=20, long_polling_timeout=20,
                    skip_pending=True, logger_level=logging.WARNING)
            except Exception as e:
                k += 1
                logger.error(f"ClubBot poll #{k}: {e}")
                time.sleep(10)
