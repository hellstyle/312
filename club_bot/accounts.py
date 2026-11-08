#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Загрузка аккаунтов из shared_secrets.cfg по пулам.
"""
import configparser
import time
import logging

logger = logging.getLogger("ClubBot.Accounts")


def load_pool(pool_flag: str) -> list[dict]:
    """Загружает аккаунты с указанным флагом из shared_secrets.cfg."""
    try:
        cfg = configparser.ConfigParser()
        cfg.read("configs/shared_secrets.cfg", encoding="utf-8")
        pool = []
        for sec in cfg.sections():
            if cfg[sec].get(pool_flag, "").strip().lower() in ("1", "true", "yes", "on"):
                pool.append({
                    "username": cfg[sec].get("username", sec),
                    "password": cfg[sec].get("password", ""),
                    "shared_secret": cfg[sec].get("shared_secret", ""),
                })
        return pool
    except Exception as e:
        logger.error(f"load_pool({pool_flag}): {e}")
        return []


def get_guard_code(shared_secret: str) -> tuple[str | None, int]:
    """Возвращает (код, секунд_осталось)."""
    if not shared_secret:
        return None, 0
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
        remaining = 30 - (int(time.time()) % 30)
        return code, remaining
    except Exception as e:
        logger.error(f"get_guard_code: {e}")
        return None, 0


POOLS = {
    "rust": "rust_pool",
    "cs": "cs_pool",
}
