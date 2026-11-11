#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Настройки российского клубного бота."""
import json, os, threading

STORAGE_DIR   = "club_bot_ru/data"
SETTINGS_FILE = f"{STORAGE_DIR}/settings.json"
_lock         = threading.Lock()

DEFAULTS = {
    "sber_number": "",
    "pc_count":    10,
    "club_name":   "Игровой клуб",
}


def load_settings() -> dict:
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in DEFAULTS.items():
                    data.setdefault(k, v)
                return data
    except Exception:
        pass
    return DEFAULTS.copy()


def save_settings(s: dict):
    os.makedirs(STORAGE_DIR, exist_ok=True)
    with _lock:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)


def get_sber() -> str:
    return load_settings().get("sber_number", "")

def set_sber(n: str):
    s = load_settings(); s["sber_number"] = n; save_settings(s)

def get_payment_number() -> str:
    return get_sber()

def get_bank_name() -> str:
    return "Сбербанк"

def get_pc_count() -> int:
    return load_settings().get("pc_count", 10)

def set_pc_count(n: int):
    s = load_settings(); s["pc_count"] = n; save_settings(s)

def get_club_name() -> str:
    return load_settings().get("club_name", "Игровой клуб")

def set_club_name(n: str):
    s = load_settings(); s["club_name"] = n; save_settings(s)
