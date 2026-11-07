#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cs_pool_handler — команда /cs в Telegram боте.

/cs      — список всех CS аккаунтов с нумерацией
/cs 2    — логин + пароль + Guard код аккаунта №2

Аккаунты в пуле помечены флагом cs_pool = true в shared_secrets.cfg.
"""

from __future__ import annotations
import configparser
import logging
import time

logger = logging.getLogger("CSPool")


def _load_cs_pool() -> list[dict]:
    """Загружает все аккаунты с флагом cs_pool из shared_secrets.cfg."""
    try:
        cfg = configparser.ConfigParser()
        cfg.read("configs/shared_secrets.cfg", encoding="utf-8")
        pool = []
        for sec in cfg.sections():
            if cfg[sec].get("cs_pool", "").strip().lower() in ("1", "true", "yes", "on"):
                pool.append({
                    "username": cfg[sec].get("username", sec),
                    "password": cfg[sec].get("password", ""),
                    "shared_secret": cfg[sec].get("shared_secret", ""),
                    "steam_id": cfg[sec].get("steam_id", ""),
                })
        return pool
    except Exception as e:
        logger.error(f"CSPool: ошибка загрузки пула: {e}")
        return []


def _get_guard_code(shared_secret: str) -> str | None:
    """Генерирует Guard код из shared_secret."""
    if not shared_secret:
        return None
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
    except Exception as e:
        logger.error(f"CSPool: ошибка генерации Guard кода: {e}")
        return None


def handle_cs_pool_command(m, bot, authorized_users: set):
    """
    /cs      — список всех аккаунтов с нумерацией
    /cs N    — логин + пароль + Guard код аккаунта №N
    """
    if m.from_user.id not in authorized_users:
        bot.send_message(m.chat.id, "⛔ Нет доступа.")
        return

    pool = _load_cs_pool()
    if not pool:
        bot.send_message(m.chat.id, "❌ Пул CS-аккаунтов пуст. Проверьте shared_secrets.cfg.")
        return

    parts = (m.text or "").strip().split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""

    # /cs без аргумента — список
    if not arg:
        lines = ["📋 <b>CS аккаунты:</b>\n"]
        for i, acc in enumerate(pool, 1):
            lines.append(f"{i}. <code>{acc['username']}</code>")
        lines.append("\nℹ️ <i>/cs [номер] — получить данные аккаунта</i>")
        bot.send_message(m.chat.id, "\n".join(lines), parse_mode="HTML")
        return

    # /cs N — данные конкретного аккаунта
    try:
        idx = int(arg)
    except ValueError:
        bot.send_message(m.chat.id, "❌ Укажите номер аккаунта. Пример: <code>/cs 2</code>", parse_mode="HTML")
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
        f"🎮 <b>CS аккаунт #{idx}</b>\n",
        f"👤 Логин: <code>{username}</code>",
        f"🔑 Пароль: <code>{password}</code>",
    ]
    if code:
        lines.append(f"🛡 Guard: <code>{code}</code>  <i>(ещё {remaining} сек)</i>")
    else:
        lines.append("🛡 Guard: <i>недоступен</i>")

    bot.send_message(m.chat.id, "\n".join(lines), parse_mode="HTML")
    logger.info(f"CSPool: выдан аккаунт #{idx} ({username}) пользователю {m.from_user.id}")
