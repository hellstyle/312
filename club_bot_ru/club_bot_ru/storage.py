#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Хранилище российского клубного бота.
Клубы — раздельные, сессии — ОБЩИЕ (club_shared).
"""
import json, os, threading

STORAGE_DIR        = "club_bot_ru/data"
SHARED_STORAGE_DIR = "club_shared/data"
CLUBS_FILE         = f"{STORAGE_DIR}/clubs.json"
SESSIONS_FILE      = f"{SHARED_STORAGE_DIR}/sessions.json"  # общая БД
_lock              = threading.Lock()


def _ensure():
    os.makedirs(STORAGE_DIR, exist_ok=True)
    os.makedirs(SHARED_STORAGE_DIR, exist_ok=True)

def load_clubs() -> dict:
    _ensure()
    try:
        if os.path.exists(CLUBS_FILE):
            with open(CLUBS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_clubs(d: dict):
    _ensure()
    with _lock:
        with open(CLUBS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)

def load_sessions() -> dict:
    _ensure()
    try:
        if os.path.exists(SESSIONS_FILE):
            with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_sessions(d: dict):
    _ensure()
    with _lock:
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
