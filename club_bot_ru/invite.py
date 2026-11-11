#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, os, secrets, time, threading

STORAGE_DIR  = "club_bot_ru/data"
INVITES_FILE = f"{STORAGE_DIR}/invites.json"
INVITE_TTL   = 48 * 3600
_lock        = threading.Lock()


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


def create_invite(pools=None) -> str:
    token = secrets.token_urlsafe(12)
    inv = _load()
    inv[token] = {"created_at": time.time(), "pools": pools or ["rust", "cs"], "used": False}
    _save(inv)
    return token


def get_invite(token: str) -> dict | None:
    data = _load().get(token)
    if not data or data.get("used"): return None
    if time.time() - data.get("created_at", 0) > INVITE_TTL: return None
    return data


def mark_used(token: str):
    inv = _load()
    if token in inv:
        inv[token]["used"] = True
        _save(inv)


def list_invites() -> list:
    now = time.time()
    return [
        {"token": t, "pools": d.get("pools", []),
         "remaining": int(INVITE_TTL - (now - d["created_at"])) // 3600}
        for t, d in _load().items()
        if not d.get("used") and now - d.get("created_at", 0) <= INVITE_TTL
    ]
