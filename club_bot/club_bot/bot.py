#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Бот для ПК клубов — весь функционал администратора на кнопках.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

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

# Пресеты часов для кнопок
HOUR_PRESETS = [1, 2, 3, 4, 6, 8, 12, 24]


class ClubBot:
    def __init__(self, token: str):
        self.bot = telebot.TeleBot(token, parse_mode="HTML", allow_sending_without_reply=True)
        self._bot_username: str | None = None
        self.clubs: dict    = load_clubs()
        self.sessions: dict = load_sessions()
        self._pending: dict = {}
        # Временное хранилище для многошаговых действий
        # {chat_id: {"action": ..., "pool": ..., "acc_idx": ...}}
        self._state: dict   = {}
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
                # Зачёркнутая базовая + финальная
                label = f"{h}ч  {int(price['base'])}→{int(price['total'])}₸"
            else:
                label = f"{h}ч  —  {int(price['total'])}₸"
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
            IKB("👤 Выдать игроку", callback_data=f"{CB_GIVE_HOURS}:sel:{pool}:{acc_idx}"),
            IKB("📱 QR код",        callback_data=f"{CB_QR_HOURS}:sel:{pool}:{acc_idx}"),
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
                    f"💰 Ставка: {price_1h['rate']} ₸/ч\n\n"
                    f"Выберите действие:",
                    cid, mid, reply_markup=kb_acc)

            # ── Выбор часов для выдачи ───────────────────────────────────
            elif data.startswith(f"{CB_GIVE_HOURS}:"):
                if not self._is_authorized(uid): return
                parts   = data.split(":")
                action  = parts[1]   # "sel"
                pool    = parts[2]
                acc_idx = int(parts[3])

                if action == "sel":
                    bot.edit_message_text(
                        f"⏱ Выберите время аренды для {pool.upper()} #{acc_idx}:",
                        cid, mid,
                        reply_markup=self._kb_hours(CB_GIVE_HOURS + ":do", pool, acc_idx))
                    return

                # action == "do" — подтверждение
                hours     = float(parts[4])
                accs      = load_pool(POOLS[pool])
                if acc_idx < 1 or acc_idx > len(accs):
                    bot.send_message(cid, "❌ Аккаунт не найден.")
                    return
                acc = accs[acc_idx - 1]

                # Предупреждение о занятости
                active = self._get_active_session_for_acc(pool, acc_idx)
                warn   = ""
                if active:
                    ends_at = active.get("ends_at")
                    if ends_at:
                        remaining = max(0, int(ends_at - time.time()))
                        h2, mn2 = remaining // 3600, (remaining % 3600) // 60
                        warn = f"\n⚠️ Аккаунт занят! Освободится через {h2}ч {mn2}м"
                    else:
                        warn = "\n⚠️ Аккаунт в активной сессии!"

                # Запрашиваем ID игрока
                self._state[cid] = {
                    "action": "give_player_id",
                    "pool": pool,
                    "acc_idx": acc_idx,
                    "hours": hours,
                    "acc": acc,
                }
                bot.edit_message_text(
                    f"✅ {pool.upper()} #{acc_idx} на {hours} ч\n"
                    f"💰 {billing.format_receipt(pool, hours, acc['username'])}{warn}\n\n"
                    f"📩 Введите Telegram ID игрока:",
                    cid, mid)

            # ── Выбор часов для QR ──────────────────────────────────────
            elif data.startswith(f"{CB_QR_HOURS}:"):
                if not self._is_authorized(uid): return
                parts   = data.split(":")
                action  = parts[1]
                pool    = parts[2]
                acc_idx = int(parts[3])

                if action == "sel":
                    bot.edit_message_text(
                        f"📱 QR для {pool.upper()} #{acc_idx} — выберите время:",
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

                active = self._get_active_session_for_acc(pool, acc_idx)
                warn   = ""
                if active:
                    ends_at = active.get("ends_at")
                    if ends_at:
                        remaining = max(0, int(ends_at - time.time()))
                        h2, mn2 = remaining // 3600, (remaining % 3600) // 60
                        warn = f"\n⚠️ Аккаунт занят ещё {h2}ч {mn2}м!"

                try:
                    from club_bot.qr_generator import create_qr_token, generate_qr_image
                    token  = create_qr_token(pool, acc_idx, hours, uid)
                    qr_img = generate_qr_image(self._get_bot_username(), token)
                    bot.send_photo(cid, qr_img,
                        caption=(
                            f"📱 <b>QR — {pool.upper()} #{acc_idx}</b>\n"
                            f"👤 {acc['username']}\n"
                            f"⏱ {hours} ч | {billing.calc_price(pool,hours)['total']} ₸"
                            + warn +
                            f"\n\n⏳ Действителен 24 часа"
                        ))
                    bot.edit_message_text("✅ QR код сгенерирован.", cid, mid)
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
                    bot.send_message(cid, "❌ Сессия не найдена.")
                    return
                code, remaining = get_guard_code(s["acc_shared_secret"])
                bot.send_message(cid,
                    f"🛡 Guard: <code>{code or '—'}</code>"
                    + (f"  <i>(ещё {remaining} сек)</i>" if code else ""))

            elif data.startswith(f"{CB_LOGGED_IN}:"):
                sid = data.split(":", 1)[1]
                s   = self.sessions.get(sid)
                if not s or not s.get("active"):
                    bot.send_message(cid, "❌ Сессия не найдена.")
                    return
                if s.get("started_at"):
                    bot.send_message(cid, "ℹ️ Таймер уже запущен.")
                    return
                now           = time.time()
                s["started_at"] = now
                s["ends_at"]    = now + s["hours"] * 3600
                s["warned"]     = False
                self._save_sessions()
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
                bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                bot.send_message(cid, "✅ Сессия завершена.")

        # Обработка текстовых сообщений — ввод ID игрока
        @bot.message_handler(func=lambda m: True, content_types=["text"])
        def on_text(m: Message):
            cid   = m.chat.id
            state = self._state.get(cid)
            if not state:
                return
            action = state.get("action")

            if action == "give_player_id":
                try:
                    player_id = int(m.text.strip())
                except ValueError:
                    bot.send_message(cid, "❌ Введите числовой ID. Попробуйте ещё раз:")
                    return
                pool    = state["pool"]
                acc_idx = state["acc_idx"]
                hours   = state["hours"]
                acc     = state["acc"]
                del self._state[cid]

                session_id = f"{player_id}_{pool}_{acc_idx}_{int(time.time())}"
                self.sessions[session_id] = {
                    "session_id":      session_id,
                    "player_id":       player_id,
                    "admin_id":        m.from_user.id,
                    "pool":            pool,
                    "acc_idx":         acc_idx,
                    "acc_username":    acc["username"],
                    "acc_password":    acc["password"],
                    "acc_shared_secret": acc["shared_secret"],
                    "hours":           hours,
                    "started_at":      None,
                    "ends_at":         None,
                    "warned":          False,
                    "active":          True,
                }
                self._save_sessions()
                text, kb = self._account_text(acc, pool, acc_idx, session_id)
                try:
                    bot.send_message(player_id, text, reply_markup=kb)
                    bot.send_message(cid,
                        f"✅ Аккаунт выдан игроку <code>{player_id}</code>!\n\n"
                        + billing.format_receipt(pool, hours, acc["username"]),
                        reply_markup=self._kb_main_menu(m.from_user.id))
                except Exception as e:
                    bot.send_message(cid, f"❌ Не удалось отправить игроку: {e}")
                    return
                self._notify_owner(
                    f"🎮 Выдан аккаунт\n\n"
                    f"👤 Игрок: <code>{player_id}</code>\n"
                    f"🎯 {pool.upper()} #{acc_idx} ({acc['username']})\n"
                    f"⏱ {hours} ч | Выдал: <code>{m.from_user.id}</code>\n\n"
                    + billing.format_receipt(pool, hours, acc["username"]))

    # ------------------------------------------------------------------
    # QR токен
    # ------------------------------------------------------------------

    def _handle_qr_token(self, m: Message, token: str):
        from club_bot.qr_generator import get_qr_token, mark_token_used
        data = get_qr_token(token)
        if not data:
            self.bot.send_message(m.chat.id,
                "❌ QR код недействителен или уже использован.\n"
                "Запросите новый у администратора.")
            return
        pool    = data["pool"]
        acc_idx = data["acc_idx"]
        hours   = data["hours"]
        admin_id = data["admin_id"]
        uid     = m.from_user.id
        accs    = load_pool(POOLS[pool])
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
            "session_id":      session_id,
            "player_id":       uid,
            "admin_id":        admin_id,
            "pool":            pool,
            "acc_idx":         acc_idx,
            "acc_username":    acc["username"],
            "acc_password":    acc["password"],
            "acc_shared_secret": acc["shared_secret"],
            "hours":           hours,
            "started_at":      None,
            "ends_at":         None,
            "warned":          False,
            "active":          True,
        }
        self._save_sessions()
        text, kb = self._account_text(acc, pool, acc_idx, session_id)
        self.bot.send_message(m.chat.id, text, reply_markup=kb)
        self._notify_owner(
            f"📱 QR активирован\n\n"
            f"👤 <code>{uid}</code>\n"
            f"🎯 {pool.upper()} #{acc_idx} ({acc['username']})\n"
            f"⏱ {hours} ч")

    # ------------------------------------------------------------------
    # Завершение сессии
    # ------------------------------------------------------------------

    def _end_session(self, session_id: str, reason: str = "expired"):
        s = self.sessions.get(session_id)
        if not s or not s.get("active"):
            return
        s["active"] = False
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
                    f"⏰ Ваша сессия завершена.\n🎮 {pool} ({acc})\n\n{receipt}")
            except Exception:
                pass
            self._notify_owner(
                f"⏰ Сессия истекла\n\n"
                f"👤 <code>{player_id}</code>\n"
                f"🎯 {pool} #{s['acc_idx']} ({acc})\n\n{receipt}")
        else:
            try:
                self.bot.send_message(player_id,
                    f"🛑 Сессия завершена.\n\n{receipt}")
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
                        if not s.get("active") or not s.get("ends_at"):
                            continue
                        ends_at = s["ends_at"]
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
