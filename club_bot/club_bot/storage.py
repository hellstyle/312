#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Хранилище данных клубного бота.
Клубы и настройки — раздельные.
Сессии — ОБЩИЕ для всех клубных ботов (club_shared).
"""
import json
import os
import threading

STORAGE_DIR        = "club_bot/data"
SHARED_STORAGE_DIR = "club_shared/data"
CLUBS_FILE         = f"{STORAGE_DIR}/clubs.json"
SESSIONS_FILE      = f"{SHARED_STORAGE_DIR}/sessions.json"  # общая БД

_lock = threading.Lock()


def _ensure_dir():
    os.makedirs(STORAGE_DIR, exist_ok=True)
    os.makedirs(SHARED_STORAGE_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Клубы
# ---------------------------------------------------------------------------

def load_clubs() -> dict:
    _ensure_dir()
    try:
        if os.path.exists(CLUBS_FILE):
            with open(CLUBS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_clubs(clubs: dict):
    _ensure_dir()
    with _lock:
        with open(CLUBS_FILE, "w", encoding="utf-8") as f:
            json.dump(clubs, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Сессии
# ---------------------------------------------------------------------------

def load_sessions() -> dict:
    _ensure_dir()
    try:
        if os.path.exists(SESSIONS_FILE):
            with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_sessions(sessions: dict):
    _ensure_dir()
    with _lock:
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(sessions, f, ensure_ascii=False, indent=2)
