#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QR генератор для клубного бота.

QR содержит deep link на бота с параметром доступа к аккаунту.
Игрок сканирует → открывает бота → получает логин/пароль/guard.

Формат ссылки: https://t.me/<bot_username>?start=qr_<token>
token хранится в qr_tokens.json
"""

import io
import os
import json
import time
import secrets
import threading
import logging

logger = logging.getLogger("ClubBot.QR")

STORAGE_DIR    = "club_shared/data"
QR_TOKENS_FILE = f"{STORAGE_DIR}/qr_tokens.json"
TOKEN_TTL = 24 * 3600  # токен живёт 24 часа

_lock = threading.Lock()


def _load_tokens() -> dict:
    try:
        if os.path.exists(QR_TOKENS_FILE):
            with open(QR_TOKENS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_tokens(tokens: dict):
    os.makedirs(STORAGE_DIR, exist_ok=True)
    with _lock:
        with open(QR_TOKENS_FILE, "w", encoding="utf-8") as f:
            json.dump(tokens, f, ensure_ascii=False, indent=2)


def create_qr_token(pool: str, acc_idx: int, hours: float, admin_id: int,
                    test: bool = False) -> str:
    """Создаёт токен для QR кода и сохраняет его."""
    token = secrets.token_urlsafe(16)
    tokens = _load_tokens()
    tokens[token] = {
        "pool":       pool,
        "acc_idx":    acc_idx,
        "hours":      hours,
        "admin_id":   admin_id,
        "created_at": time.time(),
        "used":       False,
        "test":       test,
    }
    _save_tokens(tokens)
    return token


def get_qr_token(token: str) -> dict | None:
    """Возвращает данные токена если он валиден и не использован."""
    tokens = _load_tokens()
    data = tokens.get(token)
    if not data:
        return None
    if data.get("used"):
        return None
    if time.time() - data.get("created_at", 0) > TOKEN_TTL:
        return None
    return data


def mark_token_used(token: str):
    """Помечает токен как использованный."""
    tokens = _load_tokens()
    if token in tokens:
        tokens[token]["used"] = True
        _save_tokens(tokens)


def generate_qr_image(bot_username: str, token: str) -> io.BytesIO:
    """Генерирует QR код как изображение в памяти."""
    try:
        import qrcode
        from qrcode.image.styledpil import StyledPilImage
    except ImportError:
        import qrcode

    url = f"https://t.me/{bot_username}?start=qr_{token}"

    try:
        qr = qrcode.QRCode(
            version=2,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
    except Exception as e:
        logger.error(f"generate_qr_image: {e}")
        raise

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
