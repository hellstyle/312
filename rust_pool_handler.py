#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rust_pool_handler — команда /rust в Telegram боте.

Крутит пул Rust-аккаунтов по кругу и выдаёт:
  - логин
  - пароль
  - Guard код (если есть shared_secret)

Аккаунты в пуле помечены флагом rust_pool = true в shared_secrets.cfg.
"""

from __future__ import annotations
import configparser
import logging
import time
import threading

logger = logging.getLogger("RustPool")

_lock = threading.Lock()
_current_index = 0  # текущий индекс в пуле


def _load_rust_pool() -> list[dict]:
    """Загружает все аккаунты с флагом rust_pool из shared_secrets.cfg."""
    try:
        cfg = configparser.ConfigParser()
        cfg.read("configs/shared_secrets.cfg", encoding="utf-8")
        pool = []
        for sec in cfg.sections():
            if cfg[sec].get("rust_pool", "").strip().lower() in ("1", "true", "yes", "on"):
                pool.append({
                    "username": cfg[sec].get("username", sec),
                    "password": cfg[sec].get("password", ""),
                    "shared_secret": cfg[sec].get("shared_secret", ""),
                    "steam_id": cfg[sec].get("steam_id", ""),
                })
        return pool
    except Exception as e:
        logger.error(f"RustPool: ошибка загрузки пула: {e}")
        return []


def _get_guard_code(shared_secret: str) -> str | None:
    """Получает Guard код через guard_handler."""
    if not shared_secret:
        return None
    try:
        from guard_handler import get_guard_handler
        gh = get_guard_handler()
        if gh and gh.enabled:
            # Ищем аккаунт по shared_secret среди зарегистрированных
            for acc_name, acc_data in (gh.accounts or {}).items():
                if getattr(acc_data, "shared_secret", "") == shared_secret or \
                   getattr(acc_data, "shared_secret", "").strip() == shared_secret.strip():
                    code = gh.get_guard_code_for_account(acc_name)
                    if code and not code.startswith("❌"):
                        return code
            # Fallback: генерируем напрямую через pysteamauth
            try:
                import hmac, hashlib, struct, base64
                secret = base64.b64decode(shared_secret)
                timestamp = int(time.time()) // 30
                msg = struct.pack(">Q", timestamp)
                mac = hmac.new(secret, msg, hashlib.sha1).digest()
                start = mac[-1] & 0x0F
                code_int = struct.unpack(">I", mac[start:start + 4])[0] & 0x7FFFFFFF
                chars = "23456789BCDFGHJKMNPQRTVWXY"
                code = ""
                for _ in range(5):
                    code += chars[code_int % len(chars)]
                    code_int //= len(chars)
                return code
            except Exception as ex:
                logger.error(f"RustPool: fallback guard: {ex}")
    except Exception as e:
        logger.error(f"RustPool: ошибка получения Guard кода: {e}")
    return None


def handle_rust_pool_command(m, bot, authorized_users: set):
    """
    /rust        — список всех аккаунтов с нумерацией
    /rust 3      — логин + пароль + Guard код аккаунта №3
    """
    if m.from_user.id not in authorized_users:
        bot.send_message(m.chat.id, "⛔ Нет доступа.")
        return

    pool = _load_rust_pool()
    if not pool:
        bot.send_message(m.chat.id, "❌ Пул Rust-аккаунтов пуст. Проверьте shared_secrets.cfg.")
        return

    # Парсим аргумент
    parts = (m.text or "").strip().split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""

    # /rust без аргумента — список
    if not arg:
        lines = ["📋 <b>Rust аккаунты:</b>\n"]
        for i, acc in enumerate(pool, 1):
            lines.append(f"{i}. <code>{acc['username']}</code>")
        lines.append("\nℹ️ <i>/rust [номер] — получить данные аккаунта</i>")
        bot.send_message(m.chat.id, "\n".join(lines), parse_mode="HTML")
        return

    # /rust N — данные конкретного аккаунта
    try:
        idx = int(arg)
    except ValueError:
        bot.send_message(m.chat.id, "❌ Укажите номер аккаунта. Пример: <code>/rust 3</code>", parse_mode="HTML")
        return

    if idx < 1 or idx > len(pool):
        bot.send_message(m.chat.id, f"❌ Номер должен быть от 1 до {len(pool)}.", parse_mode="HTML")
        return

    acc = pool[idx - 1]
    username = acc["username"]
    password = acc["password"]
    shared_secret = acc["shared_secret"]

    code = _get_guard_code(shared_secret) if shared_secret else None
    remaining = 30 - (int(time.time()) % 30)

    lines = [
        f"🎮 <b>Rust аккаунт #{idx}</b>\n",
        f"👤 Логин: <code>{username}</code>",
        f"🔑 Пароль: <code>{password}</code>",
    ]
    if code:
        lines.append(f"🛡 Guard: <code>{code}</code>  <i>(ещё {remaining} сек)</i>")
    else:
        lines.append("🛡 Guard: <i>недоступен</i>")

    bot.send_message(m.chat.id, "\n".join(lines), parse_mode="HTML")
    logger.info(f"RustPool: выдан аккаунт #{idx} ({username}) пользователю {m.from_user.id}")
