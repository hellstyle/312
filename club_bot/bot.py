#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Бот для ПК клубов.

Роли:
  owner   — владелец (ты), полный контроль
  admin   — администратор клуба, выдаёт аккаунты игрокам
  player  — игрок, получает аккаунт на время сессии

Флоу:
  /start  → запрос доступа → уведомление владельцу
  Владелец → [Выдать как Админ] [Отказать]
  Админ → /accounts → /give @игрок rust 3 2h
  Игрок → получает логин/пароль/guard + кнопки
         → [Запрос Guard снова] [Зашёл на аккаунт]
  После "Зашёл" → таймер → уведомление владельцу о завершении
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

import telebot
from telebot.types import (
    InlineKeyboardMarkup as IKM,
    InlineKeyboardButton as IKB,
    Message,
    CallbackQuery,
)

from club_bot.storage import load_clubs, save_clubs, load_sessions, save_sessions
from club_bot.accounts import load_pool, get_guard_code, POOLS

logger = logging.getLogger("ClubBot")

# ---------------------------------------------------------------------------
# Константы callback data
# ---------------------------------------------------------------------------
CB_GRANT_ADMIN   = "cb_grant_admin"
CB_DENY          = "cb_deny"
CB_REVOKE        = "cb_revoke"
CB_GUARD_AGAIN   = "cb_guard_again"
CB_LOGGED_IN     = "cb_logged_in"
CB_EXTEND_YES    = "cb_extend_yes"
CB_EXTEND_NO     = "cb_extend_no"

OWNER_ID = 6701153666  # твой Telegram ID

WARNING_BEFORE_END = 15 * 60   # предупреждение за 15 минут до конца
CHECK_INTERVAL     = 60        # проверка сессий каждые 60 секунд


class ClubBot:
    def __init__(self, token: str):
        self.bot = telebot.TeleBot(token, parse_mode="HTML", allow_sending_without_reply=True)
        self.clubs: dict = load_clubs()      # {user_id_str: {name, role, pools, granted_at}}
        self.sessions: dict = load_sessions()  # {session_id: {...}}
        self._pending_requests: dict = {}    # {user_id: {name, username}}
        self._register_handlers()
        self._start_session_watcher()

    # -----------------------------------------------------------------------
    # Хелперы
    # -----------------------------------------------------------------------

    def _is_owner(self, uid: int) -> bool:
        return uid == OWNER_ID

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

    def _fmt_pool_list(self, pools: list[str]) -> str:
        return ", ".join(p.upper() for p in pools) if pools else "нет"

    def _account_text(self, acc: dict, pool: str, idx: int, session_id: str | None = None) -> tuple[str, IKM]:
        code, remaining = get_guard_code(acc["shared_secret"])
        text = (
            f"🎮 <b>{pool.upper()} аккаунт #{idx}</b>\n\n"
            f"👤 Логин: <code>{acc['username']}</code>\n"
            f"🔑 Пароль: <code>{acc['password']}</code>\n"
            f"🛡 Guard: <code>{code or '—'}</code>"
            + (f"  <i>(ещё {remaining} сек)</i>" if code else "") +
            f"\n\n⚠️ <b>Передача данных аккаунта третьим лицам строго запрещена!</b>"
        )
        kb = IKM()
        row = []
        if session_id:
            row.append(IKB("🔄 Запрос Guard снова", callback_data=f"{CB_GUARD_AGAIN}:{session_id}"))
            row.append(IKB("✅ Зашёл на аккаунт", callback_data=f"{CB_LOGGED_IN}:{session_id}"))
        kb.row(*row)
        return text, kb

    # -----------------------------------------------------------------------
    # Регистрация хендлеров
    # -----------------------------------------------------------------------

    def _register_handlers(self):
        bot = self.bot

        # /start
        @bot.message_handler(commands=["start"])
        def on_start(m: Message):
            uid = m.from_user.id
            if self._is_owner(uid):
                self._send_owner_menu(m.chat.id)
                return
            club = self._get_club(uid)
            if club:
                role = club.get("role", "")
                pools = self._fmt_pool_list(club.get("pools", []))
                bot.send_message(m.chat.id,
                    f"✅ У вас есть доступ.\n"
                    f"👤 Роль: <b>{'Администратор' if role == 'admin' else 'Игрок'}</b>\n"
                    f"🎮 Пулы: <b>{pools}</b>\n\n"
                    + ("Используйте /accounts для списка аккаунтов." if role == "admin"
                       else "Ожидайте выдачи аккаунта от администратора.")
                )
                return
            # Новый пользователь — запрос
            name = m.from_user.first_name or ""
            username = m.from_user.username or str(uid)
            self._pending_requests[uid] = {"name": name, "username": username}
            bot.send_message(m.chat.id,
                "👋 Добро пожаловать!\n\n"
                "Ваш запрос на доступ отправлен владельцу.\n"
                "Ожидайте подтверждения."
            )
            kb = IKM()
            kb.row(
                IKB("✅ Выдать как Админ", callback_data=f"{CB_GRANT_ADMIN}:{uid}"),
                IKB("❌ Отказать", callback_data=f"{CB_DENY}:{uid}"),
            )
            self._notify_owner(
                f"🔔 Новый запрос доступа\n\n"
                f"👤 {name} (@{username})\n"
                f"🆔 ID: <code>{uid}</code>",
                kb
            )

        # /clubs — список клубов (только владелец)
        @bot.message_handler(commands=["clubs"])
        def on_clubs(m: Message):
            if not self._is_owner(m.from_user.id):
                return
            if not self.clubs:
                bot.send_message(m.chat.id, "📋 Нет зарегистрированных клубов.")
                return
            lines = ["📋 <b>Клубы и права:</b>\n"]
            for uid_str, info in self.clubs.items():
                role = "Админ" if info.get("role") == "admin" else "Игрок"
                pools = self._fmt_pool_list(info.get("pools", []))
                lines.append(
                    f"👤 {info.get('name', '?')} (@{info.get('username', '?')})\n"
                    f"   Роль: {role} | Пулы: {pools}\n"
                    f"   ID: <code>{uid_str}</code>"
                )
                kb = IKM()
                kb.row(IKB("🚫 Отозвать доступ", callback_data=f"{CB_REVOKE}:{uid_str}"))
                bot.send_message(m.chat.id, lines[-1], reply_markup=kb)
                lines = [lines[0]]

        # /addpool <user_id> <pool> — добавить пул (только владелец)
        @bot.message_handler(commands=["addpool"])
        def on_addpool(m: Message):
            if not self._is_owner(m.from_user.id):
                return
            parts = m.text.strip().split()
            if len(parts) < 3:
                bot.send_message(m.chat.id, "Использование: /addpool <user_id> <rust|cs>")
                return
            uid_str, pool = parts[1], parts[2].lower()
            if uid_str not in self.clubs:
                bot.send_message(m.chat.id, "❌ Пользователь не найден.")
                return
            if pool not in POOLS:
                bot.send_message(m.chat.id, f"❌ Неизвестный пул. Доступны: {', '.join(POOLS.keys())}")
                return
            pools = self.clubs[uid_str].setdefault("pools", [])
            if pool not in pools:
                pools.append(pool)
                self._save()
            bot.send_message(m.chat.id, f"✅ Пул {pool.upper()} добавлен пользователю {uid_str}.")

        # /removepool <user_id> <pool>
        @bot.message_handler(commands=["removepool"])
        def on_removepool(m: Message):
            if not self._is_owner(m.from_user.id):
                return
            parts = m.text.strip().split()
            if len(parts) < 3:
                bot.send_message(m.chat.id, "Использование: /removepool <user_id> <rust|cs>")
                return
            uid_str, pool = parts[1], parts[2].lower()
            if uid_str not in self.clubs:
                bot.send_message(m.chat.id, "❌ Пользователь не найден.")
                return
            pools = self.clubs[uid_str].get("pools", [])
            if pool in pools:
                pools.remove(pool)
                self._save()
            bot.send_message(m.chat.id, f"✅ Пул {pool.upper()} удалён у пользователя {uid_str}.")

        # /accounts — список доступных аккаунтов (для админа клуба)
        @bot.message_handler(commands=["accounts"])
        def on_accounts(m: Message):
            uid = m.from_user.id
            club = self._get_club(uid)
            if not club or club.get("role") != "admin":
                if not self._is_owner(uid):
                    bot.send_message(m.chat.id, "⛔ Нет доступа.")
                    return
                club = {"pools": list(POOLS.keys())}
            pools = club.get("pools", [])
            if not pools:
                bot.send_message(m.chat.id, "❌ Вам не назначены пулы аккаунтов.")
                return
            for pool in pools:
                accs = load_pool(POOLS[pool])
                if not accs:
                    bot.send_message(m.chat.id, f"❌ Пул {pool.upper()} пуст.")
                    continue
                lines = [f"🎮 <b>{pool.upper()} аккаунты:</b>\n"]
                for i, acc in enumerate(accs, 1):
                    lines.append(f"{i}. <code>{acc['username']}</code>")
                lines.append(f"\nℹ️ <i>/give [id игрока] {pool} [номер] [часы]</i>")
                bot.send_message(m.chat.id, "\n".join(lines))

        # /give <player_id> <pool> <номер> <часы>
        @bot.message_handler(commands=["give"])
        def on_give(m: Message):
            uid = m.from_user.id
            club = self._get_club(uid)
            is_owner = self._is_owner(uid)
            if not is_owner and (not club or club.get("role") != "admin"):
                bot.send_message(m.chat.id, "⛔ Нет доступа.")
                return
            parts = m.text.strip().split()
            if len(parts) < 5:
                bot.send_message(m.chat.id,
                    "Использование: /give [id игрока] [rust|cs] [номер] [часы]\n"
                    "Пример: /give 123456789 rust 3 2"
                )
                return
            try:
                player_id = int(parts[1])
                pool = parts[2].lower()
                acc_idx = int(parts[3])
                hours = float(parts[4])
            except ValueError:
                bot.send_message(m.chat.id, "❌ Неверный формат.")
                return
            if pool not in POOLS:
                bot.send_message(m.chat.id, f"❌ Неизвестный пул: {pool}")
                return
            allowed_pools = list(POOLS.keys()) if is_owner else (club.get("pools", []) if club else [])
            if pool not in allowed_pools:
                bot.send_message(m.chat.id, f"❌ У вас нет доступа к пулу {pool.upper()}.")
                return
            accs = load_pool(POOLS[pool])
            if acc_idx < 1 or acc_idx > len(accs):
                bot.send_message(m.chat.id, f"❌ Номер аккаунта должен быть от 1 до {len(accs)}.")
                return
            acc = accs[acc_idx - 1]
            # Создаём сессию
            session_id = f"{player_id}_{pool}_{acc_idx}_{int(time.time())}"
            session = {
                "session_id": session_id,
                "player_id": player_id,
                "admin_id": uid,
                "pool": pool,
                "acc_idx": acc_idx,
                "acc_username": acc["username"],
                "acc_password": acc["password"],
                "acc_shared_secret": acc["shared_secret"],
                "hours": hours,
                "started_at": None,       # заполнится после "Зашёл на аккаунт"
                "ends_at": None,
                "warned": False,
                "active": True,
            }
            self.sessions[session_id] = session
            self._save_sessions()
            # Отправляем игроку
            text, kb = self._account_text(acc, pool, acc_idx, session_id)
            try:
                bot.send_message(player_id, text, reply_markup=kb)
                bot.send_message(m.chat.id,
                    f"✅ Аккаунт {acc['username']} выдан игроку <code>{player_id}</code> на {hours} ч.")
            except Exception as e:
                bot.send_message(m.chat.id, f"❌ Не удалось отправить игроку: {e}")
                return
            # Уведомляем владельца
            self._notify_owner(
                f"🎮 Выдан аккаунт\n\n"
                f"👤 Игрок: <code>{player_id}</code>\n"
                f"🎯 Пул: {pool.upper()} #{acc_idx} ({acc['username']})\n"
                f"⏱ Время: {hours} ч\n"
                f"👨‍💼 Выдал: <code>{uid}</code>"
            )

        # /sessions — активные сессии (только владелец)
        @bot.message_handler(commands=["sessions"])
        def on_sessions(m: Message):
            if not self._is_owner(m.from_user.id):
                return
            active = {k: v for k, v in self.sessions.items() if v.get("active")}
            if not active:
                bot.send_message(m.chat.id, "📋 Нет активных сессий.")
                return
            for sid, s in active.items():
                ends_at = s.get("ends_at")
                if ends_at:
                    remaining = max(0, int(ends_at - time.time()))
                    time_str = f"{remaining // 3600}ч {(remaining % 3600) // 60}м"
                else:
                    time_str = "таймер не запущен"
                kb = IKM()
                kb.row(IKB("🛑 Завершить сессию", callback_data=f"end_session:{sid}"))
                bot.send_message(m.chat.id,
                    f"🎮 <b>Сессия</b>\n"
                    f"👤 Игрок: <code>{s['player_id']}</code>\n"
                    f"🎯 {s['pool'].upper()} #{s['acc_idx']} ({s['acc_username']})\n"
                    f"⏱ Осталось: {time_str}",
                    reply_markup=kb
                )

        # Callbacks
        @bot.callback_query_handler(func=lambda c: True)
        def on_callback(c: CallbackQuery):
            data = c.data
            uid = c.from_user.id
            bot.answer_callback_query(c.id)

            # --- Владелец выдаёт права ---
            if data.startswith(f"{CB_GRANT_ADMIN}:"):
                if not self._is_owner(uid):
                    return
                target_id = int(data.split(":")[1])
                req = self._pending_requests.pop(target_id, {})
                self.clubs[str(target_id)] = {
                    "name": req.get("name", "?"),
                    "username": req.get("username", "?"),
                    "role": "admin",
                    "pools": list(POOLS.keys()),
                    "granted_at": datetime.now().isoformat(),
                }
                self._save()
                bot.edit_message_reply_markup(c.message.chat.id, c.message.id, reply_markup=None)
                bot.send_message(c.message.chat.id,
                    f"✅ Выданы права Администратора пользователю <code>{target_id}</code>.")
                try:
                    bot.send_message(target_id,
                        "✅ Ваш запрос одобрен!\n\n"
                        "👨‍💼 Роль: <b>Администратор клуба</b>\n"
                        f"🎮 Доступные пулы: <b>{self._fmt_pool_list(list(POOLS.keys()))}</b>\n\n"
                        "Используйте /accounts для просмотра аккаунтов.\n"
                        "Используйте /give для выдачи игроку."
                    )
                except Exception:
                    pass

            # --- Владелец отказывает ---
            elif data.startswith(f"{CB_DENY}:"):
                if not self._is_owner(uid):
                    return
                target_id = int(data.split(":")[1])
                self._pending_requests.pop(target_id, None)
                bot.edit_message_reply_markup(c.message.chat.id, c.message.id, reply_markup=None)
                bot.send_message(c.message.chat.id, f"❌ Отказано пользователю <code>{target_id}</code>.")
                try:
                    bot.send_message(target_id, "❌ В доступе отказано.")
                except Exception:
                    pass

            # --- Владелец отзывает доступ ---
            elif data.startswith(f"{CB_REVOKE}:"):
                if not self._is_owner(uid):
                    return
                target_id_str = data.split(":")[1]
                self.clubs.pop(target_id_str, None)
                self._save()
                bot.edit_message_reply_markup(c.message.chat.id, c.message.id, reply_markup=None)
                bot.send_message(c.message.chat.id,
                    f"✅ Доступ отозван у пользователя <code>{target_id_str}</code>.")
                try:
                    bot.send_message(int(target_id_str), "🚫 Ваш доступ был отозван администратором.")
                except Exception:
                    pass

            # --- Игрок запрашивает Guard снова ---
            elif data.startswith(f"{CB_GUARD_AGAIN}:"):
                session_id = data.split(":", 1)[1]
                s = self.sessions.get(session_id)
                if not s or not s.get("active"):
                    bot.send_message(c.message.chat.id, "❌ Сессия не найдена или завершена.")
                    return
                acc = {
                    "username": s["acc_username"],
                    "password": s["acc_password"],
                    "shared_secret": s["acc_shared_secret"],
                }
                code, remaining = get_guard_code(s["acc_shared_secret"])
                bot.send_message(c.message.chat.id,
                    f"🛡 Guard код: <code>{code or '—'}</code>"
                    + (f"  <i>(ещё {remaining} сек)</i>" if code else "")
                )

            # --- Игрок зашёл на аккаунт — запускаем таймер ---
            elif data.startswith(f"{CB_LOGGED_IN}:"):
                session_id = data.split(":", 1)[1]
                s = self.sessions.get(session_id)
                if not s or not s.get("active"):
                    bot.send_message(c.message.chat.id, "❌ Сессия не найдена.")
                    return
                if s.get("started_at"):
                    bot.send_message(c.message.chat.id, "ℹ️ Таймер уже запущен.")
                    return
                now = time.time()
                s["started_at"] = now
                s["ends_at"] = now + s["hours"] * 3600
                s["warned"] = False
                self._save_sessions()
                # Убираем кнопки
                bot.edit_message_reply_markup(c.message.chat.id, c.message.id, reply_markup=None)
                ends_str = datetime.fromtimestamp(s["ends_at"]).strftime("%H:%M")
                bot.send_message(c.message.chat.id,
                    f"✅ Таймер запущен!\n"
                    f"⏱ Сессия активна до <b>{ends_str}</b> ({s['hours']} ч)\n\n"
                    f"⚠️ Напоминаем: передача данных аккаунта третьим лицам запрещена!"
                )
                self._notify_owner(
                    f"▶️ Сессия запущена\n\n"
                    f"👤 Игрок: <code>{s['player_id']}</code>\n"
                    f"🎯 {s['pool'].upper()} #{s['acc_idx']} ({s['acc_username']})\n"
                    f"⏱ До: {ends_str} ({s['hours']} ч)"
                )

            # --- Продление сессии ---
            elif data.startswith(f"{CB_EXTEND_YES}:"):
                if not self._is_owner(uid):
                    return
                parts = data.split(":")
                session_id, extra_hours = parts[1], float(parts[2])
                s = self.sessions.get(session_id)
                if not s or not s.get("active"):
                    return
                s["ends_at"] += extra_hours * 3600
                s["warned"] = False
                self._save_sessions()
                bot.edit_message_reply_markup(c.message.chat.id, c.message.id, reply_markup=None)
                ends_str = datetime.fromtimestamp(s["ends_at"]).strftime("%H:%M")
                bot.send_message(c.message.chat.id,
                    f"✅ Сессия продлена на {extra_hours} ч. Новое время окончания: {ends_str}")
                try:
                    bot.send_message(s["player_id"],
                        f"✅ Ваша сессия продлена на {extra_hours} ч.\n"
                        f"⏱ Новое время окончания: <b>{ends_str}</b>")
                except Exception:
                    pass

            elif data.startswith(f"{CB_EXTEND_NO}:"):
                if not self._is_owner(uid):
                    return
                session_id = data.split(":")[1]
                bot.edit_message_reply_markup(c.message.chat.id, c.message.id, reply_markup=None)
                bot.send_message(c.message.chat.id, "❌ Продление отклонено.")
                s = self.sessions.get(session_id)
                if s:
                    try:
                        bot.send_message(s["player_id"], "❌ Продление сессии отклонено.")
                    except Exception:
                        pass

            # --- Завершить сессию вручную ---
            elif data.startswith("end_session:"):
                if not self._is_owner(uid):
                    return
                session_id = data.split(":", 1)[1]
                self._end_session(session_id, reason="manual")
                bot.edit_message_reply_markup(c.message.chat.id, c.message.id, reply_markup=None)
                bot.send_message(c.message.chat.id, "✅ Сессия завершена.")

        # /extend <session_id> <часы> — запрос продления от игрока
        @bot.message_handler(commands=["extend"])
        def on_extend(m: Message):
            uid = str(m.from_user.id)
            # Ищем активную сессию этого игрока
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
                IKB(f"✅ Продлить на {extra_hours} ч",
                    callback_data=f"{CB_EXTEND_YES}:{sid}:{extra_hours}"),
                IKB("❌ Отклонить", callback_data=f"{CB_EXTEND_NO}:{sid}"),
            )
            self._notify_owner(
                f"⏳ Запрос на продление сессии\n\n"
                f"👤 Игрок: <code>{uid}</code>\n"
                f"🎯 {s['pool'].upper()} #{s['acc_idx']} ({s['acc_username']})\n"
                f"➕ Запрошено: {extra_hours} ч",
                kb
            )
            bot.send_message(m.chat.id,
                f"✅ Запрос на продление ({extra_hours} ч) отправлен администратору.")

    # -----------------------------------------------------------------------
    # Завершение сессии
    # -----------------------------------------------------------------------

    def _end_session(self, session_id: str, reason: str = "expired"):
        s = self.sessions.get(session_id)
        if not s or not s.get("active"):
            return
        s["active"] = False
        self._save_sessions()
        player_id = s["player_id"]
        acc = s["acc_username"]
        pool = s["pool"].upper()
        if reason == "expired":
            msg_player = (
                f"⏰ Ваша сессия завершена.\n"
                f"🎮 Аккаунт {pool} ({acc}) деактивирован.\n"
                f"Спасибо за использование!"
            )
            msg_owner = (
                f"⏰ Сессия истекла\n\n"
                f"👤 Игрок: <code>{player_id}</code>\n"
                f"🎯 {pool} #{s['acc_idx']} ({acc})\n"
                f"❗ Необходимо завершить сессию вручную"
            )
        else:
            msg_player = f"🛑 Ваша сессия была завершена администратором."
            msg_owner = (
                f"🛑 Сессия завершена вручную\n\n"
                f"👤 Игрок: <code>{player_id}</code>\n"
                f"🎯 {pool} #{s['acc_idx']} ({acc})"
            )
        try:
            self.bot.send_message(player_id, msg_player)
        except Exception:
            pass
        self._notify_owner(msg_owner)

    # -----------------------------------------------------------------------
    # Фоновый наблюдатель сессий
    # -----------------------------------------------------------------------

    def _start_session_watcher(self):
        def watch():
            while True:
                try:
                    now = time.time()
                    for sid, s in list(self.sessions.items()):
                        if not s.get("active") or not s.get("ends_at"):
                            continue
                        ends_at = s["ends_at"]
                        # Предупреждение за 15 минут
                        if not s.get("warned") and 0 < ends_at - now <= WARNING_BEFORE_END:
                            s["warned"] = True
                            self._save_sessions()
                            remaining_min = int((ends_at - now) / 60)
                            try:
                                self.bot.send_message(
                                    s["player_id"],
                                    f"⚠️ До конца сессии осталось <b>{remaining_min} мин</b>!\n"
                                    f"Используйте /extend [часы] для запроса продления."
                                )
                            except Exception:
                                pass
                            self._notify_owner(
                                f"⚠️ Сессия заканчивается через {remaining_min} мин\n\n"
                                f"👤 Игрок: <code>{s['player_id']}</code>\n"
                                f"🎯 {s['pool'].upper()} #{s['acc_idx']} ({s['acc_username']})"
                            )
                        # Сессия истекла
                        if now >= ends_at:
                            self._end_session(sid, reason="expired")
                except Exception as e:
                    logger.error(f"session_watcher: {e}")
                time.sleep(CHECK_INTERVAL)

        t = threading.Thread(target=watch, name="ClubBotSessionWatcher", daemon=True)
        t.start()

    # -----------------------------------------------------------------------
    # Меню владельца
    # -----------------------------------------------------------------------

    def _send_owner_menu(self, chat_id: int):
        self.bot.send_message(chat_id,
            "👑 <b>Панель владельца — Клубный бот</b>\n\n"
            "/clubs — список клубов и права\n"
            "/sessions — активные сессии\n"
            "/addpool [id] [rust|cs] — добавить пул\n"
            "/removepool [id] [rust|cs] — убрать пул\n"
            "/give [id игрока] [пул] [номер] [часы] — выдать аккаунт\n"
        )

    # -----------------------------------------------------------------------
    # Запуск
    # -----------------------------------------------------------------------

    def run(self):
        logger.info("ClubBot запущен")
        k_err = 0
        while True:
            try:
                self.bot.infinity_polling(
                    timeout=20,
                    long_polling_timeout=20,
                    skip_pending=True,
                    logger_level=logging.WARNING,
                )
            except Exception as e:
                k_err += 1
                logger.error(f"ClubBot polling error #{k_err}: {e}")
                time.sleep(10)
