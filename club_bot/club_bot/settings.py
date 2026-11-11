#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Настройки казахстанского клубного бота.
"""
import json
import os
import threading

STORAGE_DIR   = "club_bot/data"
SETTINGS_FILE = f"{STORAGE_DIR}/settings.json"
_lock         = threading.Lock()

DEFAULTS = {
    "kaspi_number": "",
    "pc_count":     10,
    "club_name":    "Игровой клуб",
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


def get_kaspi() -> str:
    return load_settings().get("kaspi_number", "")

def set_kaspi(number: str):
    s = load_settings(); s["kaspi_number"] = number; save_settings(s)

def get_payment_number() -> str:
    return get_kaspi()

def get_bank_name() -> str:
    return "Kaspi"

def get_pc_count() -> int:
    return load_settings().get("pc_count", 10)

def set_pc_count(count: int):
    s = load_settings(); s["pc_count"] = count; save_settings(s)

def get_club_name() -> str:
    return load_settings().get("club_name", "Игровой клуб")

def set_club_name(name: str):
    s = load_settings(); s["club_name"] = name; save_settings(s)
