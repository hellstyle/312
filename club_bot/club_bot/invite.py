#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Система инвайт-ссылок для регистрации администраторов.
"""
import json
import os
import secrets
import time
import threading

STORAGE_DIR   = "club_bot/data"
INVITES_FILE  = f"{STORAGE_DIR}/invites.json"
INVITE_TTL    = 48 * 3600  # ссылка живёт 48 часов
_lock         = threading.Lock()


def _load() -> dict:
    try:
        if os.path.exists(INVITES_FILE):
            with open(INVITES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save(data: dict):
    os.makedirs(STORAGE_DIR, exist_ok=True)
    with _lock:
        with open(INVITES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def create_invite(pools: list[str] | None = None) -> str:
    """Создаёт одноразовый инвайт токен."""
    token   = secrets.token_urlsafe(12)
    invites = _load()
    invites[token] = {
        "created_at": time.time(),
        "pools":      pools or ["rust", "cs"],
        "used":       False,
    }
    _save(invites)
    return token


def get_invite(token: str) -> dict | None:
    """Возвращает данные инвайта если он валиден."""
    invites = _load()
    data    = invites.get(token)
    if not data:
        return None
    if data.get("used"):
        return None
    if time.time() - data.get("created_at", 0) > INVITE_TTL:
        return None
    return data


def mark_used(token: str):
    invites = _load()
    if token in invites:
        invites[token]["used"] = True
        _save(invites)


def list_invites() -> list[dict]:
    """Список активных инвайтов."""
    invites = _load()
    now     = time.time()
    result  = []
    for token, data in invites.items():
        if not data.get("used") and now - data.get("created_at", 0) <= INVITE_TTL:
            remaining = int(INVITE_TTL - (now - data["created_at"])) // 3600
            result.append({
                "token":     token,
                "pools":     data.get("pools", []),
                "remaining": remaining,
            })
    return result
