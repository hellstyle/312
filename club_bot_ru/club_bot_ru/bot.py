#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Российский клубный бот — Сбербанк, ₽.
Идентичен казахстанскому но с другим хранилищем и валютой.
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

from club_bot_ru.storage  import load_clubs, save_clubs, load_sessions, save_sessions
from club_bot_ru.settings import (get_sber, set_sber, get_pc_count, set_pc_count,
                                   get_club_name, set_club_name, get_payment_number,
                                   get_bank_name)
from club_bot_ru.invite   import create_invite, get_invite, mark_used, list_invites
from club_bot_ru          import billing
# Аккаунты берём из общего модуля
from club_bot.accounts    import load_pool, get_guard_code, POOLS

logger = logging.getLogger("ClubBotRU")

# ---------------------------------------------------------------------------
# Callback константы
# ---------------------------------------------------------------------------
CB_GRANT_ADMIN = "rg_adm"
CB_DENY        = "rg_deny"
CB_REVOKE      = "rg_rev"
CB_GUARD_AGAIN = "rg_grd"
CB_LOGGED_IN   = "rg_log"
CB_EXTEND_YES  = "rg_ext_y"
CB_EXTEND_NO   = "rg_ext_n"
CB_EXTEND_WANT = "rx_want"
CB_END_SESSION = "rg_end"

CB_MENU_MAIN    = "rm_main"
CB_MENU_ACCS    = "rm_accs"
CB_POOL_ACCS    = "rm_pool"
CB_ACC_ACTION   = "rm_acc"
CB_QR_HOURS     = "rm_qr_h"
CB_BILLING_MENU = "rm_bill"

CB_PAY_GAME    = "rp_game"
CB_PAY_COUNT   = "rp_cnt"
CB_PAY_PC      = "rp_pc"
CB_PAY_HOURS   = "rp_hours"
CB_PAY_CONFIRM = "rp_confirm"
CB_PAY_APPROVE = "rp_approve"
CB_PAY_REJECT  = "rp_reject"

OWNER_ID              = 6701153666
WARNING_BEFORE_END    = 15 * 60
CHECK_INTERVAL        = 60
QR_ACTIVATION_TIMEOUT = 30 * 60
HOUR_PRESETS          = [1, 2, 3, 4, 6, 8, 12, 24]


class ClubBotRU:
    def __init__(self, token: str):
        self.bot = telebot.TeleBot(token, parse_mode="HTML", allow_sending_without_reply=True)
        self._bot_username: str | None = None
        self.clubs: dict    = load_clubs()
        self.sessions: dict = load_sessions()
        self._pending: dict = {}
        self._pending_payments: dict = {}
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

    def _save(self):
        save_clubs(self.clubs)

    def _save_sessions(self):
        save_sessions(self.sessions)

    def _notify_owner(self, text: str, kb=None):
        try:
            self.bot.send_message(OWNER_ID, text, reply_markup=kb)
        except Exception as e:
            logger.error(f"_notify_owner: {e}")

    def _fmt_pools(self, pools: list) -> str:
        return ", ".join(p.upper() for p in pools) if pools else "нет"

    def _get_bot_username(self) -> str:
        if not self._bot_username:
            try:
                self._bot_username = self.bot.get_me().username
            except Exception:
                self._bot_username = "unknown"
        return self._bot_username

    def _get_active_session_for_acc(self, pool: str, acc_idx: int) -> dict | None:
        for s in self.sessions.values():
            if s.get("active") and s.get("pool") == pool and s.get("acc_idx") == acc_idx:
                return s
        return None

    def _acc_status_line(self, pool: str, idx: int) -> str:
        s = self._get_active_session_for_acc(pool, idx)
        if not s: return "🟢"
        ends_at = s.get("ends_at")
        if ends_at:
            rem = max(0, int(ends_at - time.time()))
            return f"🔴 {rem//3600}ч{(rem%3600)//60}м"
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
    # Клавиатуры
    # ------------------------------------------------------------------

    def _kb_main_menu(self, uid: int) -> IKM:
        kb = IKM()
        kb.row(IKB("🎮 Аккаунты",        callback_data=CB_MENU_ACCS))
        kb.row(IKB("📋 Активные сессии", callback_data=f"{CB_MENU_MAIN}:sessions"))
        kb.row(IKB("💰 Бухгалтерия",     callback_data=CB_BILLING_MENU))
        if self._is_owner(uid):
            kb.row(IKB("👥 Клубы",        callback_data=f"{CB_MENU_MAIN}:clubs"))
        return kb

    def _kb_pool_select(self, action: str, uid: int) -> IKM:
        kb    = IKM()
        pools = list(POOLS.keys()) if self._is_owner(uid) else \
                self.clubs.get(str(uid), {}).get("pools", [])
        for p in pools:
            accs  = load_pool(POOLS[p])
            free  = sum(1 for i in range(1, len(accs)+1)
                        if not self._get_active_session_for_acc(p, i))
            kb.row(IKB(f"🎮 {p.upper()}  —  {free}/{len(accs)} свободно",
                       callback_data=f"{CB_POOL_ACCS}:{action}:{p}"))
        kb.row(IKB("◀️ Назад", callback_data=f"{CB_MENU_MAIN}:back"))
        return kb

    def _kb_accounts(self, pool: str, action: str, uid: int) -> IKM:
        kb   = IKM()
        accs = load_pool(POOLS[pool])
        row  = []
        for i, acc in enumerate(accs, 1):
            status = self._acc_status_line(pool, i)
            btn    = IKB(f"#{i} {acc['username'][:10]} {status}",
                         callback_data=f"{CB_ACC_ACTION}:{action}:{pool}:{i}")
            row.append(btn)
            if len(row) == 2:
                kb.row(*row); row = []
        if row: kb.row(*row)
        kb.row(IKB("◀️ Назад", callback_data=f"{CB_POOL_ACCS}:back:{pool}"))
        return kb

    def _kb_acc_actions(self, pool: str, acc_idx: int) -> tuple[IKM, str, str]:
        kb     = IKM()
        active = self._get_active_session_for_acc(pool, acc_idx)
        accs   = load_pool(POOLS[pool])
        acc    = accs[acc_idx - 1] if acc_idx <= len(accs) else None
        acc_name = acc["username"] if acc else f"#{acc_idx}"
        if active:
            ends_at = active.get("ends_at")
            if ends_at:
                rem = max(0, int(ends_at - time.time()))
                status = f"🔴 Занят — {rem//3600}ч {(rem%3600)//60}м"
            else:
                status = "🟡 Занят (таймер не запущен)"
            kb.row(IKB("🛑 Завершить сессию",
                       callback_data=f"{CB_END_SESSION}:{active['session_id']}"))
        else:
            status = "🟢 Свободен"
        kb.row(IKB("📱 Выдать (QR)", callback_data=f"{CB_QR_HOURS}:sel:{pool}:{acc_idx}"))
        kb.row(IKB("◀️ Назад", callback_data=f"{CB_POOL_ACCS}:give:{pool}"))
        return kb, status, acc_name

    def _kb_hours(self, cb_prefix: str, pool: str, acc_idx: int) -> IKM:
        kb = IKM(); row = []
        for h in HOUR_PRESETS:
            price = billing.calc_price(pool, h)
            label = (f"{h}ч  {int(price['base'])}→{int(price['total'])}{billing.CURRENCY}"
                     if price["discount_pct"] else
                     f"{h}ч  —  {int(price['total'])}{billing.CURRENCY}")
            row.append(IKB(label, callback_data=f"{cb_prefix}:{pool}:{acc_idx}:{h}"))
            if len(row) == 2:
                kb.row(*row); row = []
        if row: kb.row(*row)
        kb.row(IKB("◀️ Назад", callback_data=f"{CB_ACC_ACTION}:back:{pool}:{acc_idx}"))
        return kb

    def _kb_billing(self) -> IKM:
        kb = IKM()
        kb.row(IKB("📅 Сегодня",   callback_data=f"{CB_BILLING_MENU}:day"))
        kb.row(IKB("📆 7 дней",    callback_data=f"{CB_BILLING_MENU}:7"),
               IKB("📆 30 дней",   callback_data=f"{CB_BILLING_MENU}:30"))
        kb.row(IKB("📋 Детально",  callback_data=f"{CB_BILLING_MENU}:detail"))
        kb.row(IKB("◀️ Назад",     callback_data=f"{CB_MENU_MAIN}:back"))
        return kb

    def _kb_game_select(self) -> IKM:
        kb = IKM()
        kb.row(IKB("🎮 Rust",    callback_data=f"{CB_PAY_GAME}:rust"))
        kb.row(IKB("🎮 CS GO 2", callback_data=f"{CB_PAY_GAME}:cs"))
        return kb

    def _kb_acc_count(self, game: str) -> IKM:
        kb = IKM()
        kb.row(
            IKB("1 аккаунт",           callback_data=f"{CB_PAY_COUNT}:{game}:1"),
            IKB("2 аккаунта  🎁 −15%", callback_data=f"{CB_PAY_COUNT}:{game}:2"),
        )
        return kb

    def _kb_pc_select(self, game: str, count: int) -> IKM:
        kb = IKM(); row = []
        for i in range(1, get_pc_count() + 1):
            row.append(IKB(str(i), callback_data=f"{CB_PAY_PC}:{game}:{count}:{i}"))
            if len(row) == 5:
                kb.row(*row); row = []
        if row: kb.row(*row)
        return kb

    def _kb_pay_hours(self, game: str, count: int, pc: int) -> IKM:
        kb = IKM(); row = []
        for h in HOUR_PRESETS:
            price = billing.calc_price(game, h, count)
            label = (f"{h}ч  {int(price['base'])}→{int(price['total'])}{billing.CURRENCY}"
                     if price["discount_pct"] else
                     f"{h}ч  —  {int(price['total'])}{billing.CURRENCY}")
            row.append(IKB(label, callback_data=f"{CB_PAY_HOURS}:{game}:{count}:{pc}:{h}"))
            if len(row) == 2:
                kb.row(*row); row = []
        if row: kb.row(*row)
        return kb

    # ------------------------------------------------------------------
    # Автообновление Guard
    # ------------------------------------------------------------------

    def _start_guard_autoupdate(self, player_id: int, session_id: str, shared_secret: str):
        stop_event = threading.Event()
        if not hasattr(self, "_guard_stops"):
            self._guard_stops = {}
        self._guard_stops[session_id] = stop_event

        def _loop():
            time.sleep(25)
            while not stop_event.is_set():
                s = self.sessions.get(session_id)
                if not s or not s.get("active") or s.get("started_at"):
                    break
                code, remaining = get_guard_code(shared_secret)
                if code:
                    try:
                        self.bot.send_message(player_id,
                            f"🔄 <b>Обновлённый Guard код:</b>\n"
                            f"<code>{code}</code>  <i>(ещё {remaining} сек)</i>")
                    except Exception:
                        break
                stop_event.wait(timeout=25)

        threading.Thread(target=_loop, name=f"GuardRU_{session_id}", daemon=True).start()

    def _stop_guard_autoupdate(self, session_id: str):
        if hasattr(self, "_guard_stops"):
            ev = self._guard_stops.pop(session_id, None)
            if ev: ev.set()

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

            if arg.startswith("qr_"):
                self._handle_qr_token(m, arg[3:])
                return

            if arg.startswith("admin_"):
                token = arg[6:]
                invite = get_invite(token)
                if not invite:
                    bot.send_message(m.chat.id, "❌ Ссылка недействительна или уже использована.")
                    return
                mark_used(token)
                name     = m.from_user.first_name or ""
                username = m.from_user.username or str(uid)
                self.clubs[str(uid)] = {
                    "name": name, "username": username, "role": "admin",
                    "pools": invite.get("pools", ["rust", "cs"]),
                    "granted_at": datetime.now().isoformat(),
                }
                self._save()
                bot.send_message(m.chat.id,
                    f"✅ <b>Вы зарегистрированы как администратор!</b>\n\n"
                    f"🏢 Клуб: <b>{get_club_name()}</b>\n"
                    f"🎮 Пулы: <b>{self._fmt_pools(invite.get('pools', ['rust','cs']))}</b>\n\n"
                    f"/menu — открыть панель управления")
                self._notify_owner(
                    f"👨‍💼 Новый администратор\n\n"
                    f"👤 {name} (@{username})\n🆔 <code>{uid}</code>")
                return

            if self._is_owner(uid):
                bot.send_message(m.chat.id,
                    f"👑 <b>Панель владельца — {get_club_name()}</b>\n\nВыберите раздел:",
                    reply_markup=self._kb_main_menu(uid))
                bot.send_message(m.chat.id,
                    "⚙️ <b>Настройки:</b>\n"
                    f"/setname — название клуба (сейчас: <b>{get_club_name()}</b>)\n"
                    f"/setsber — номер Сбер (сейчас: <code>{get_sber() or 'не задан'}</code>)\n"
                    f"/setpc — кол-во мест (сейчас: <code>{get_pc_count()}</code>)\n"
                    f"/genlink — ссылка для регистрации админа\n"
                    "/resetbilling — сбросить бухгалтерию")
                return

            if self._is_admin(uid):
                club = self.clubs.get(str(uid), {})
                bot.send_message(m.chat.id,
                    f"👨‍💼 <b>Панель администратора — {get_club_name()}</b>\n"
                    f"🎮 Пулы: <b>{self._fmt_pools(club.get('pools', []))}</b>\n\n"
                    "Выберите раздел:", reply_markup=self._kb_main_menu(uid))
                return

            # Активная сессия игрока
            active_s = [s for s in self.sessions.values()
                        if s.get("active") and str(s.get("player_id")) == str(uid)]
            if active_s:
                s = active_s[0]
                ends_at = s.get("ends_at")
                if ends_at:
                    rem = max(0, int(ends_at - time.time()))
                    bot.send_message(m.chat.id,
                        f"🎮 Активная сессия.\n⏱ Осталось: {rem//3600}ч {(rem%3600)//60}м\n\n"
                        f"/extend [ч] — запросить продление\n/status — статус сессии")
                return

            # Новый игрок
            num = get_payment_number()
            if not num:
                bot.send_message(m.chat.id,
                    f"👋 Добро пожаловать в <b>{get_club_name()}</b>!\n\n"
                    "⚠️ Касса пока не настроена. Обратитесь к администратору.")
                return

            bot.send_message(m.chat.id,
                f"👋 <b>Добро пожаловать в {get_club_name()}!</b>\n\n"
                "🎮 Аренда аккаунтов для игры.\n\n"
                "⚠️ <b>Обратите внимание:</b>\n"
                "• Оплата за аренду аккаунта\n"
                "• Компьютерное время оплачивается <b>отдельно</b>\n"
                "• 2 аккаунта — <b>скидка 15%</b>!\n\n"
                "Выберите игру:", reply_markup=self._kb_game_select())

        @bot.message_handler(commands=["menu"])
        def on_menu(m: Message):
            if not self._is_authorized(m.from_user.id): return
            bot.send_message(m.chat.id, "📋 Главное меню:",
                             reply_markup=self._kb_main_menu(m.from_user.id))

        @bot.message_handler(commands=["setname"])
        def on_setname(m: Message):
            if not self._is_owner(m.from_user.id): return
            parts = m.text.strip().split(maxsplit=1)
            if len(parts) < 2:
                bot.send_message(m.chat.id, f"Текущее: <b>{get_club_name()}</b>\n/setname Название")
                return
            set_club_name(parts[1].strip())
            bot.send_message(m.chat.id, f"✅ Название: <b>{parts[1].strip()}</b>")

        @bot.message_handler(commands=["setsber"])
        def on_setsber(m: Message):
            if not self._is_owner(m.from_user.id): return
            parts = m.text.strip().split(maxsplit=1)
            if len(parts) < 2:
                bot.send_message(m.chat.id,
                    f"Текущий номер Сбер: <code>{get_sber() or 'не задан'}</code>\n/setsber 79XXXXXXXXX")
                return
            set_sber(parts[1].strip())
            bot.send_message(m.chat.id, f"✅ Сбер: <code>{parts[1].strip()}</code>")

        @bot.message_handler(commands=["setpc"])
        def on_setpc(m: Message):
            if not self._is_owner(m.from_user.id): return
            parts = m.text.strip().split(maxsplit=1)
            if len(parts) < 2:
                bot.send_message(m.chat.id, f"Мест: <code>{get_pc_count()}</code>\n/setpc 10")
                return
            try:
                set_pc_count(int(parts[1]))
                bot.send_message(m.chat.id, f"✅ Мест: <code>{parts[1]}</code>")
            except ValueError:
                bot.send_message(m.chat.id, "❌ Укажите число.")

        @bot.message_handler(commands=["genlink"])
        def on_genlink(m: Message):
            if not self._is_owner(m.from_user.id): return
            token = create_invite()
            link  = f"https://t.me/{self._get_bot_username()}?start=admin_{token}"
            bot.send_message(m.chat.id,
                f"🔗 <b>Ссылка регистрации админа:</b>\n\n<code>{link}</code>\n\n"
                f"⏳ 48 часов | 🔁 Одноразовая")

        @bot.message_handler(commands=["resetbilling"])
        def on_resetbilling(m: Message):
            if not self._is_owner(m.from_user.id): return
            billing.reset_billing()
            bot.send_message(m.chat.id, "✅ Бухгалтерия сброшена.")

        @bot.message_handler(commands=["status"])
        def on_status(m: Message):
            uid = str(m.from_user.id)
            active = [(sid, s) for sid, s in self.sessions.items()
                      if s.get("active") and str(s.get("player_id")) == uid]
            if not active:
                bot.send_message(m.chat.id,
                    "❌ Нет активной сессии.\n\n"
                    "⚠️ За новым QR обратитесь к администратору клуба.")
                return
            sid, s = active[0]
            ends_at = s.get("ends_at")
            if not s.get("started_at"):
                bot.send_message(m.chat.id, "⏳ Таймер не запущен. Нажмите ✅ Зашёл на аккаунт.")
                return
            rem    = max(0, int(ends_at - time.time()))
            h, mn  = rem // 3600, (rem % 3600) // 60
            ends_s = datetime.fromtimestamp(ends_at).strftime("%H:%M")
            bot.send_message(m.chat.id,
                f"🎮 <b>{s['pool'].upper()} #{s['acc_idx']}</b>\n\n"
                f"⏱ Осталось: <b>{h}ч {mn}м</b>\n"
                f"🕐 До: <b>{ends_s}</b>\n\n"
                f"/extend [ч] — запросить продление")

        @bot.message_handler(commands=["extend"])
        def on_extend(m: Message):
            uid = str(m.from_user.id)
            active_sessions = [(sid, s) for sid, s in self.sessions.items()
                               if s.get("active") and str(s.get("player_id")) == uid]
            if not active_sessions:
                bot.send_message(m.chat.id,
                    "❌ Нет активной сессии.\n\n"
                    "⚠️ По истечении сессии обратитесь к администратору за новым QR кодом.")
                return
            parts = m.text.strip().split()
            try:
                eh = float(parts[1]) if len(parts) > 1 else 1.0
            except ValueError:
                eh = 1.0
            sid, s = active_sessions[0]
            kb = IKM()
            kb.row(
                IKB(f"✅ Да, продлить на {eh} ч",
                    callback_data=f"{CB_EXTEND_WANT}:{sid}:{eh}"),
                IKB("❌ Нет", callback_data="ru_extend_cancel"),
            )
            bot.send_message(m.chat.id,
                f"⏳ Продлить сессию на <b>{eh} ч</b>?\n\n"
                f"⚠️ После истечения сессии — обратитесь к администратору за новым QR.",
                reply_markup=kb)

        @bot.callback_query_handler(func=lambda c: True)
        def on_callback(c: CallbackQuery):
            data = c.data
            uid  = c.from_user.id
            cid  = c.message.chat.id
            mid  = c.message.id
            bot.answer_callback_query(c.id)

            # ── Флоу оплаты ───────────────────────────────────────────
            if data.startswith(f"{CB_PAY_GAME}:"):
                game = data.split(":")[1]
                name = "Rust" if game == "rust" else "CS GO 2"
                bot.edit_message_text(
                    f"🎮 Игра: <b>{name}</b>\n\n"
                    f"👥 Выберите количество аккаунтов:\n"
                    f"<i>🎁 2 аккаунта — скидка 15%!</i>",
                    cid, mid, reply_markup=self._kb_acc_count(game))

            elif data.startswith(f"{CB_PAY_COUNT}:"):
                _, game, count = data.split(":")
                count = int(count)
                name  = "Rust" if game == "rust" else "CS GO 2"
                bot.edit_message_text(
                    f"🎮 {name} | 👥 {count} акк."
                    + (" 🎁 −15%" if count >= 2 else "") +
                    "\n\n💺 Выберите номер места:",
                    cid, mid, reply_markup=self._kb_pc_select(game, count))

            elif data.startswith(f"{CB_PAY_PC}:"):
                _, game, count, pc = data.split(":")
                count = int(count); pc = int(pc)
                name  = "Rust" if game == "rust" else "CS GO 2"
                bot.edit_message_text(
                    f"🎮 {name} | 👥 {count} акк. | 💺 #{pc}\n\n"
                    f"⏱ Выберите время:\n\n"
                    f"⚠️ Оплата аккаунта — отдельно от компьютерного времени!",
                    cid, mid, reply_markup=self._kb_pay_hours(game, count, pc))

            elif data.startswith(f"{CB_PAY_HOURS}:"):
                parts = data.split(":")
                game  = parts[1]; count = int(parts[2])
                pc    = int(parts[3]); hours = float(parts[4])
                price = billing.calc_price(game, hours, count)
                num   = get_payment_number()
                name  = "Rust" if game == "rust" else "CS GO 2"

                self._pending_payments[uid] = {
                    "game": game, "pc": pc, "hours": hours, "count": count,
                    "amount": price["total"], "name": name, "user_id": uid,
                    "username": f"@{c.from_user.username}" if c.from_user.username else str(uid),
                }
                kb = IKM()
                kb.row(IKB("✅ Я оплатил", callback_data=f"{CB_PAY_CONFIRM}:{uid}"))
                disc = f"🎁 Скидка {price['discount_pct']}%\n" if price["discount_pct"] else ""
                bot.edit_message_text(
                    f"🧾 <b>Детали аренды</b>\n\n"
                    f"🎮 {name} | 👥 {count} акк. | 💺 #{pc} | ⏱ {hours} ч\n"
                    f"💰 Сумма: <b>{int(price['total'])} {billing.CURRENCY}</b>\n{disc}\n"
                    f"📱 <b>Оплатите через {get_bank_name()}:</b>\n<code>{num}</code>\n\n"
                    f"⚠️ Оплата компьютерного времени — отдельно администратору.\n\n"
                    f"После оплаты нажмите кнопку:",
                    cid, mid, reply_markup=kb)

            elif data.startswith(f"{CB_PAY_CONFIRM}:"):
                pay_uid  = int(data.split(":")[1])
                payment  = self._pending_payments.get(pay_uid)
                if not payment:
                    bot.answer_callback_query(c.id, "❌ Данные не найдены.", show_alert=True)
                    return
                bot.edit_message_text("⏳ Оплата на проверке. Ожидайте...", cid, mid)
                kb = IKM()
                kb.row(
                    IKB("✅ Подтвердить", callback_data=f"{CB_PAY_APPROVE}:{pay_uid}"),
                    IKB("❌ Отклонить",   callback_data=f"{CB_PAY_REJECT}:{pay_uid}"),
                )
                self._notify_owner(
                    f"💳 <b>Запрос на оплату (RU)</b>\n\n"
                    f"👤 {payment['username']} (<code>{pay_uid}</code>)\n"
                    f"🎮 {payment['name']} | 👥 {payment['count']} акк.\n"
                    f"💺 #{payment['pc']} | ⏱ {payment['hours']} ч\n"
                    f"💰 {int(payment['amount'])} {billing.CURRENCY}", kb)

            elif data.startswith(f"{CB_PAY_APPROVE}:"):
                if not self._is_owner(uid): return
                pay_uid = int(data.split(":")[1])
                payment = self._pending_payments.pop(pay_uid, None)
                if not payment: return
                bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                bot.send_message(cid, f"✅ Подтверждено для <code>{pay_uid}</code>.")

                pool  = payment["game"]
                hours = payment["hours"]
                count = payment.get("count", 1)
                accs  = load_pool(POOLS[pool])
                free_accs = []
                for i, a in enumerate(accs, 1):
                    if not self._get_active_session_for_acc(pool, i):
                        free_accs.append((i, a))
                    if len(free_accs) == count:
                        break

                if len(free_accs) < count:
                    bot.send_message(cid, f"⚠️ Нет {count} свободных аккаунтов!")
                    try:
                        bot.send_message(pay_uid,
                            "✅ Оплата подтверждена!\n⚠️ Временно нет аккаунтов. Администратор свяжется.")
                    except Exception:
                        pass
                    return

                try:
                    bot.send_message(pay_uid,
                        f"✅ <b>Оплата подтверждена!</b>\n\n"
                        f"🎮 {payment['name']} | 👥 {count} акк. | 💺 #{payment['pc']} | ⏱ {hours} ч\n\n"
                        f"Данные аккаунтов ниже 👇")
                except Exception as e:
                    bot.send_message(cid, f"❌ Не удалось отправить: {e}"); return

                for acc_idx, acc in free_accs:
                    sid = f"{pay_uid}_{pool}_{acc_idx}_{int(time.time())}"
                    self.sessions[sid] = {
                        "session_id": sid, "player_id": pay_uid, "admin_id": OWNER_ID,
                        "pool": pool, "acc_idx": acc_idx, "acc_count": count,
                        "acc_username": acc["username"], "acc_password": acc["password"],
                        "acc_shared_secret": acc["shared_secret"],
                        "hours": hours, "started_at": None, "ends_at": None,
                        "warned": False, "active": True,
                        "created_at": time.time(), "pc_number": payment["pc"],
                    }
                    self._save_sessions()
                    text, kb2 = self._account_text(acc, pool, acc_idx, sid)
                    try:
                        bot.send_message(pay_uid, text, reply_markup=kb2)
                        self._start_guard_autoupdate(pay_uid, sid, acc["shared_secret"])
                    except Exception:
                        pass

            elif data.startswith(f"{CB_PAY_REJECT}:"):
                if not self._is_owner(uid): return
                pay_uid = int(data.split(":")[1])
                self._pending_payments.pop(pay_uid, None)
                bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                bot.send_message(cid, f"❌ Отклонено.")
                try:
                    bot.send_message(pay_uid, "❌ Оплата не подтверждена. Обратитесь к администратору.")
                except Exception:
                    pass

            # ── Навигация ─────────────────────────────────────────────
            elif data in (f"{CB_MENU_MAIN}:back", f"{CB_MENU_MAIN}:sessions",
                          f"{CB_MENU_MAIN}:clubs"):
                if not self._is_authorized(uid): return
                if data == f"{CB_MENU_MAIN}:back":
                    bot.edit_message_text("📋 Главное меню:", cid, mid,
                                          reply_markup=self._kb_main_menu(uid))
                elif data == f"{CB_MENU_MAIN}:sessions":
                    active = {k: v for k, v in self.sessions.items() if v.get("active")}
                    if not active:
                        bot.edit_message_text("📋 Нет сессий.", cid, mid,
                                              reply_markup=self._kb_main_menu(uid))
                        return
                    bot.edit_message_text("📋 Активные сессии:", cid, mid,
                                          reply_markup=self._kb_main_menu(uid))
                    for sid, s in active.items():
                        ends_at  = s.get("ends_at")
                        time_str = (f"{max(0,int(ends_at-time.time()))//3600}ч "
                                    f"{(max(0,int(ends_at-time.time()))%3600)//60}м"
                                    if ends_at else "таймер не запущен")
                        kb2 = IKM()
                        kb2.row(IKB("🛑 Завершить", callback_data=f"{CB_END_SESSION}:{sid}"))
                        bot.send_message(cid,
                            f"🎮 {s['pool'].upper()} #{s['acc_idx']} ({s['acc_username']})\n"
                            f"👤 <code>{s['player_id']}</code> | ⏱ {time_str}",
                            reply_markup=kb2)
                elif data == f"{CB_MENU_MAIN}:clubs":
                    if not self._is_owner(uid): return
                    if not self.clubs:
                        bot.edit_message_text("👥 Нет клубов.", cid, mid,
                                              reply_markup=self._kb_main_menu(uid))
                        return
                    for uid_str, info in self.clubs.items():
                        kb2 = IKM()
                        kb2.row(IKB("🚫 Отозвать", callback_data=f"{CB_REVOKE}:{uid_str}"))
                        bot.send_message(cid,
                            f"👤 {info.get('name','?')} (@{info.get('username','?')})\n"
                            f"ID: <code>{uid_str}</code>", reply_markup=kb2)

            elif data == CB_MENU_ACCS:
                if not self._is_authorized(uid): return
                bot.edit_message_text("🎮 Выберите пул:", cid, mid,
                                      reply_markup=self._kb_pool_select("give", uid))

            elif data.startswith(f"{CB_POOL_ACCS}:"):
                if not self._is_authorized(uid): return
                _, action, pool = data.split(":", 2)
                if action == "back":
                    bot.edit_message_text("🎮 Выберите пул:", cid, mid,
                                          reply_markup=self._kb_pool_select("give", uid))
                    return
                bot.edit_message_text(
                    f"🎮 <b>{pool.upper()}</b>  🟢 свободен  🔴 занят",
                    cid, mid, reply_markup=self._kb_accounts(pool, action, uid))

            elif data.startswith(f"{CB_ACC_ACTION}:"):
                if not self._is_authorized(uid): return
                parts    = data.split(":")
                pool     = parts[2]; acc_idx = int(parts[3])
                accs     = load_pool(POOLS[pool])
                acc      = accs[acc_idx - 1] if acc_idx <= len(accs) else None
                kb_acc, status, acc_name = self._kb_acc_actions(pool, acc_idx)
                price_1h = billing.calc_price(pool, 1)
                bot.edit_message_text(
                    f"🎮 <b>{pool.upper()} #{acc_idx}</b> — {acc_name}\n"
                    f"📊 {status}\n💰 {price_1h['rate']} {billing.CURRENCY}/ч\n\nВыберите действие:",
                    cid, mid, reply_markup=kb_acc)

            elif data.startswith(f"{CB_QR_HOURS}:"):
                if not self._is_authorized(uid): return
                parts   = data.split(":")
                action  = parts[1]; pool = parts[2]; acc_idx = int(parts[3])
                if action == "sel":
                    bot.edit_message_text(
                        f"⏱ Время для {pool.upper()} #{acc_idx}:\n\n"
                        f"⚠️ Выдавайте только после оплаты!",
                        cid, mid, reply_markup=self._kb_hours(f"{CB_QR_HOURS}:do", pool, acc_idx))
                    return
                hours = float(parts[4])
                accs  = load_pool(POOLS[pool])
                if acc_idx < 1 or acc_idx > len(accs):
                    bot.send_message(cid, "❌ Аккаунт не найден."); return
                acc    = accs[acc_idx - 1]
                active = self._get_active_session_for_acc(pool, acc_idx)
                if active:
                    ends_at = active.get("ends_at")
                    if ends_at and active.get("started_at"):
                        rem = max(0, int(ends_at - time.time()))
                        h2, mn2 = rem // 3600, (rem % 3600) // 60
                        bot.edit_message_text(
                            f"🔴 <b>Аккаунт занят!</b>\n\n"
                            f"{pool.upper()} #{acc_idx} — {acc['username']}\n"
                            f"Освободится через {h2}ч {mn2}м",
                            cid, mid, reply_markup=self._kb_accounts(pool, "give", uid))
                        return
                    elif not active.get("started_at"):
                        bot.edit_message_text(
                            f"🟡 <b>Аккаунт зарезервирован!</b>\n\n"
                            f"QR выдан, игрок не зашёл.\nВыберите другой.",
                            cid, mid, reply_markup=self._kb_accounts(pool, "give", uid))
                        return
                try:
                    from club_bot.qr_generator import create_qr_token, generate_qr_image
                    token  = create_qr_token(pool, acc_idx, hours, uid)
                    qr_img = generate_qr_image(self._get_bot_username(), token)
                    bot.send_photo(cid, qr_img,
                        caption=(
                            f"📱 <b>QR — {pool.upper()} #{acc_idx}</b>\n"
                            f"👤 {acc['username']}\n"
                            f"⏱ {hours} ч | {billing.calc_price(pool,hours)['total']} {billing.CURRENCY}\n\n"
                            f"📲 Игрок сканирует QR → Telegram бот → данные аккаунта\n"
                            f"⏳ Действителен 24 часа\n"
                            f"⚠️ Передача данных запрещена!"
                        ))
                    bot.edit_message_text(
                        f"✅ QR выдан для {pool.upper()} #{acc_idx} на {hours} ч\n\n📋 Главное меню:",
                        cid, mid, reply_markup=self._kb_main_menu(uid))
                except ImportError:
                    bot.send_message(cid, "❌ pip install qrcode[pil]")
                except Exception as e:
                    bot.send_message(cid, f"❌ Ошибка QR: {e}")

            elif data.startswith(CB_BILLING_MENU):
                if not self._is_authorized(uid): return
                sub = data.split(":", 1)[1] if ":" in data else ""
                if not sub:
                    bot.edit_message_text("💰 Бухгалтерия:", cid, mid,
                                          reply_markup=self._kb_billing())
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
                    bot.send_message(cid, billing.report_day())

            elif data.startswith(f"{CB_GRANT_ADMIN}:"):
                if not self._is_owner(uid): return
                target_id = int(data.split(":")[1])
                req = self._pending.pop(target_id, {})
                self.clubs[str(target_id)] = {
                    "name": req.get("name","?"), "username": req.get("username","?"),
                    "role": "admin", "pools": list(POOLS.keys()),
                    "granted_at": datetime.now().isoformat(),
                }
                self._save()
                bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                bot.send_message(cid, f"✅ Права выданы <code>{target_id}</code>.")
                try:
                    bot.send_message(target_id,
                        f"✅ Вы администратор!\n🏢 {get_club_name()}\n/menu — панель управления")
                except Exception:
                    pass

            elif data.startswith(f"{CB_REVOKE}:"):
                if not self._is_owner(uid): return
                tid = data.split(":")[1]
                self.clubs.pop(tid, None); self._save()
                bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                bot.send_message(cid, f"✅ Доступ отозван у <code>{tid}</code>.")
                try:
                    bot.send_message(int(tid), "🚫 Доступ отозван.")
                except Exception:
                    pass

            elif data.startswith(f"{CB_GUARD_AGAIN}:"):
                sid = data.split(":", 1)[1]
                s   = self.sessions.get(sid)
                if not s or not s.get("active"):
                    bot.answer_callback_query(c.id, "❌ Сессия завершена.", show_alert=True)
                    try: bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                    except Exception: pass
                    return
                code, rem = get_guard_code(s["acc_shared_secret"])
                bot.send_message(cid,
                    f"🛡 Guard: <code>{code or '—'}</code>"
                    + (f"  <i>(ещё {rem} сек)</i>" if code else ""))

            elif data.startswith(f"{CB_LOGGED_IN}:"):
                sid = data.split(":", 1)[1]
                s   = self.sessions.get(sid)
                if not s or not s.get("active"):
                    bot.answer_callback_query(c.id, "❌ Сессия завершена.", show_alert=True)
                    try: bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                    except Exception: pass
                    return
                if s.get("started_at"):
                    bot.answer_callback_query(c.id, "ℹ️ Таймер уже запущен.")
                    return
                now            = time.time()
                s["started_at"] = now
                s["ends_at"]    = now + s["hours"] * 3600
                s["warned"]     = False
                self._save_sessions()
                self._stop_guard_autoupdate(sid)
                bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                ends_s = datetime.fromtimestamp(s["ends_at"]).strftime("%H:%M")
                bot.send_message(cid,
                    f"✅ Таймер запущен! До <b>{ends_s}</b> ({s['hours']} ч)\n\n"
                    f"⚠️ Передача данных запрещена!\n/extend [ч] — продление")
                self._notify_owner(
                    f"▶️ Сессия (RU)\n👤 <code>{uid}</code>\n"
                    f"🎯 {s['pool'].upper()} #{s['acc_idx']} ({s['acc_username']})\n"
                    f"⏱ До {ends_s}")

            elif data.startswith(f"{CB_EXTEND_WANT}:"):
                _, sid, eh = data.split(":")
                s = self.sessions.get(sid)
                if not s or not s.get("active"):
                    bot.answer_callback_query(c.id, "❌ Сессия завершена.", show_alert=True)
                    return
                bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                bot.send_message(cid,
                    f"✅ Запрос на продление {eh} ч отправлен. Ожидайте.")
                kb = IKM()
                kb.row(
                    IKB(f"✅ Продлить {eh} ч", callback_data=f"{CB_EXTEND_YES}:{sid}:{eh}"),
                    IKB("❌ Отклонить",          callback_data=f"{CB_EXTEND_NO}:{sid}"),
                )
                self._notify_owner(
                    f"⏳ Продление (RU)\n👤 <code>{uid}</code>\n"
                    f"🎯 {s['pool'].upper()} #{s['acc_idx']} ({s['acc_username']})\n"
                    f"➕ {eh} ч", kb)

            elif data == "ru_extend_cancel":
                bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                bot.send_message(cid, "ℹ️ Запрос отменён.")

            elif data.startswith(f"{CB_EXTEND_YES}:"):
                if not self._is_owner(uid): return
                _, sid, eh = data.split(":")
                s = self.sessions.get(sid)
                if not s or not s.get("active"): return
                s["ends_at"] += float(eh) * 3600
                s["warned"]   = False
                self._save_sessions()
                bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                ends_s = datetime.fromtimestamp(s["ends_at"]).strftime("%H:%M")
                bot.send_message(cid, f"✅ Продлено на {eh} ч. До {ends_s}")
                try:
                    bot.send_message(s["player_id"],
                        f"✅ Сессия продлена на {eh} ч. До <b>{ends_s}</b>")
                except Exception: pass

            elif data.startswith(f"{CB_EXTEND_NO}:"):
                if not self._is_owner(uid): return
                sid = data.split(":")[1]
                bot.edit_message_reply_markup(cid, mid, reply_markup=None)
                bot.send_message(cid, "❌ Продление отклонено.")
                s = self.sessions.get(sid)
                if s:
                    try:
                        bot.send_message(s["player_id"],
                            "❌ Продление отклонено.\n\n"
                            "⚠️ По истечении сессии обратитесь за новым QR.")
                    except Exception: pass

            elif data.startswith(f"{CB_END_SESSION}:"):
                if not self._is_authorized(uid): return
                sid = data.split(":", 1)[1]
                self._end_session(sid, "manual")
                bot.edit_message_text("✅ Сессия завершена.\n\n📋 Главное меню:",
                                      cid, mid, reply_markup=self._kb_main_menu(uid))

        @bot.message_handler(func=lambda m: True,
                             content_types=["text","photo","sticker","document"])
        def on_any(m: Message):
            uid = m.from_user.id
            if self._is_authorized(uid): return
            has_active = any(s.get("active") and str(s.get("player_id")) == str(uid)
                             for s in self.sessions.values())
            if has_active: return
            had = any(not s.get("active") and str(s.get("player_id")) == str(uid)
                      for s in self.sessions.values())
            if had: return  # молча игнорируем

    # ------------------------------------------------------------------
    # QR токен
    # ------------------------------------------------------------------

    def _handle_qr_token(self, m: Message, token: str):
        from club_bot.qr_generator import get_qr_token, mark_token_used
        data = get_qr_token(token)
        if not data:
            self.bot.send_message(m.chat.id,
                "❌ QR недействителен или использован. Обратитесь к администратору.")
            return
        pool    = data["pool"]; acc_idx = data["acc_idx"]
        hours   = data["hours"]; admin_id = data["admin_id"]
        uid     = m.from_user.id
        accs    = load_pool(POOLS[pool])
        if acc_idx < 1 or acc_idx > len(accs):
            self.bot.send_message(m.chat.id, "❌ Аккаунт не найден."); return
        acc = accs[acc_idx - 1]
        mark_token_used(token)
        sid = f"{uid}_{pool}_{acc_idx}_{int(time.time())}"
        self.sessions[sid] = {
            "session_id": sid, "player_id": uid, "admin_id": admin_id,
            "pool": pool, "acc_idx": acc_idx, "acc_count": 1,
            "acc_username": acc["username"], "acc_password": acc["password"],
            "acc_shared_secret": acc["shared_secret"],
            "hours": hours, "started_at": None, "ends_at": None,
            "warned": False, "active": True, "created_at": time.time(),
        }
        self._save_sessions()
        text, kb = self._account_text(acc, pool, acc_idx, sid)
        self.bot.send_message(m.chat.id, text, reply_markup=kb)
        self._start_guard_autoupdate(uid, sid, acc["shared_secret"])
        self._notify_owner(
            f"📱 QR (RU)\n👤 <code>{uid}</code>\n"
            f"🎯 {pool.upper()} #{acc_idx} ({acc['username']})\n⏱ {hours} ч")

    # ------------------------------------------------------------------
    # Завершение сессии
    # ------------------------------------------------------------------

    def _end_session(self, session_id: str, reason: str = "expired"):
        s = self.sessions.get(session_id)
        if not s or not s.get("active"): return
        s["active"] = False; s["ended_at"] = time.time()
        self._save_sessions()
        try: billing.record_session(s)
        except Exception as e: logger.error(f"billing: {e}")
        player_id = s["player_id"]
        pool      = s["pool"].upper()
        acc       = s["acc_username"]
        receipt   = billing.format_receipt(s["pool"], s["hours"], acc, s.get("acc_count", 1))
        if reason == "not_activated":
            try:
                self.bot.send_message(player_id,
                    "⏰ Сессия отменена — аккаунт не был активирован за 30 мин.\n"
                    "Обратитесь к администратору за новым QR.")
            except Exception: pass
            self._notify_owner(
                f"⚠️ Не активирована (RU)\n👤 <code>{player_id}</code>\n"
                f"🎯 {pool} #{s['acc_idx']} ({acc})")
        elif reason == "expired":
            try:
                self.bot.send_message(player_id,
                    f"⏰ Сессия завершена.\n{receipt}\n\n"
                    f"🔒 Доступ закрыт.\n"
                    f"⚠️ Для продолжения подойдите к администратору за новым QR кодом.")
            except Exception: pass
            self._notify_owner(
                f"⏰ Сессия истекла (RU)\n👤 <code>{player_id}</code>\n"
                f"🎯 {pool} #{s['acc_idx']} ({acc})\n\n{receipt}")
        else:
            try:
                self.bot.send_message(player_id,
                    f"🛑 Сессия завершена.\n{receipt}\n\n🔒 Доступ закрыт.")
            except Exception: pass
            self._notify_owner(
                f"🛑 Завершена (RU)\n👤 <code>{player_id}</code>\n"
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
                        if not s.get("active"): continue
                        ends_at    = s.get("ends_at")
                        started_at = s.get("started_at")
                        created_at = s.get("created_at", now)
                        if ends_at:
                            if not s.get("warned") and 0 < ends_at - now <= WARNING_BEFORE_END:
                                s["warned"] = True; self._save_sessions()
                                rem = int((ends_at - now) / 60)
                                try:
                                    self.bot.send_message(s["player_id"],
                                        f"⚠️ До конца сессии <b>{rem} мин</b>!\n"
                                        f"/extend [ч] — запросить продление.\n"
                                        f"⚠️ По истечении обратитесь за новым QR.")
                                except Exception: pass
                                self._notify_owner(
                                    f"⚠️ Заканчивается (RU) через {rem} мин\n"
                                    f"👤 <code>{s['player_id']}</code>\n"
                                    f"🎯 {s['pool'].upper()} #{s['acc_idx']} ({s['acc_username']})")
                            if now >= ends_at:
                                self._end_session(sid, "expired")
                        elif not started_at:
                            if now - created_at > QR_ACTIVATION_TIMEOUT:
                                self._end_session(sid, "not_activated")
                except Exception as e:
                    logger.error(f"watcher RU: {e}")
                time.sleep(CHECK_INTERVAL)

        threading.Thread(target=watch, name="ClubBotRUWatcher", daemon=True).start()

    # ------------------------------------------------------------------
    # Запуск
    # ------------------------------------------------------------------

    def run(self):
        logger.info("ClubBotRU запущен")
        k = 0
        while True:
            try:
                self.bot.infinity_polling(
                    timeout=20, long_polling_timeout=20,
                    skip_pending=True, logger_level=logging.WARNING)
            except Exception as e:
                k += 1
                logger.error(f"ClubBotRU poll #{k}: {e}")
                time.sleep(10)
