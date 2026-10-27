#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
steam_kick — «выкинуть» арендатора из Steam-аккаунта по окончании аренды
или при попытке изменить настройки профиля.

ВАЖНО — ЧЕСТНО О ПРОВЕРКЕ:
  Реальный «кик» (revoke_sessions / change_password) выполняет вход в Steam как
  владелец аккаунта (login + password + Steam Guard код из shared_secret) и затем
  отзывает все другие сессии. Эта часть НЕ проверялась против живого Steam —
  Steam периодически меняет auth-эндпоинты, и неверный вызов может привести к
  кулдауну или ограничениям аккаунта. Поэтому live-кик по умолчанию ОТКЛЮЧЁН
  (флаг ENABLE_UNVERIFIED_STEAM_KICK = False) и должен быть протестирован на
  burner-аккаунте перед включением на реальной аренде.

  Включённая по умолчанию ветка method="notify" — безопасна: она только логирует
  и уведомляет владельца бота (через Telegram), что аренда закончилась и аккаунт
  нужно кикнуть. Это проверяемо и не несёт риска для аккаунтов.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from cardinal import Cardinal

logger = logging.getLogger("SteamKick")

# ПЕРЕКЛЮЧАТЕЛЬ ЛИЧНОГО РИСКА. По умолчанию False — live-действия не выполняются.
# Включайте только после теста на burner-аккаунте.
ENABLE_UNVERIFIED_STEAM_KICK = False

STEAM_API_BASE = "https://api.steampowered.com"


# ---------------------------------------------------------------------------
# Безопасный метод по умолчанию — только уведомление владельца.
# ---------------------------------------------------------------------------
def kick_notify(cardinal: "Cardinal", steam_login: str, session_id: str,
                reason: str = "rental_expired") -> dict:
    """
    Безопасный «кик»: уведомляет владельца бота, что аренда закончилась/профиль
    изменён и аккаунт нужно отключить. Реальных действий со Steam не выполняет.
    """
    msg = (f"⛔ Аренда завершена — кикните аккаунт\n\n"
           f"👤 Аккаунт: <code>{steam_login}</code>\n"
           f"🆔 Сессия: {session_id}\n"
           f"📋 Причина: {reason}\n\n"
           f"Войдите в аккаунт и завершите все сессии (Steam → Аккаунт → "
           f"Безопасность / Sign out of all sessions).")
    logger.info(f"kick_notify: {steam_login} ({reason}) — {session_id}")
    try:
        if cardinal is not None and getattr(cardinal, "telegram", None) and cardinal.telegram.bot:
            owner_chat_id = getattr(cardinal.telegram, "owner_chat_id", None)
            if owner_chat_id:
                import threading
                threading.Thread(
                    target=cardinal.telegram.bot.send_message,
                    args=(owner_chat_id, msg),
                    daemon=True,
                ).start()
    except Exception as e:
        logger.error(f"kick_notify: не удалось отправить уведомление: {e}")
    return {"ok": True, "action": "notify", "steam_login": steam_login, "reason": reason}


# ---------------------------------------------------------------------------
# Live-кик — НЕПРОВЕРЕНО против живого Steam. См. предупреждение в шапке модуля.
# ---------------------------------------------------------------------------
def _rsa_encrypt_password(password: str, mod_b64: str, exp_b64: str) -> str:
    """Шифрует пароль RSA (PKCS1v1.5), как требует Steam UpdateAuthSession."""
    try:
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend
        n = int.from_bytes(base64.b64decode(mod_b64), "big")
        e = int.from_bytes(base64.b64decode(exp_b64), "big")
        pub = rsa.RSAPublicNumbers(e, n).public_key(default_backend())
        encrypted = pub.encrypt(password.encode("utf-8"), padding.PKCS1v15())
        return base64.b64encode(encrypted).decode("ascii")
    except Exception as exc:
        logger.error(f"_rsa_encrypt_password: {exc}")
        raise


def _steam_login(steam_login: str, password: str, shared_secret: str):
    """
    Вход в Steam как владелец аккаунта. НЕПРОВЕРЕНО.

    Возвращает requests.Session с установленной cookie steamLoginSecure либо None.
    """
    import requests
    from shared_secret_guard import SharedSecretGuard

    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (steam_kick; FunPayCardinal)"})

    # 1) RSA public key для шифрования пароля
    r = sess.get(f"{STEAM_API_BASE}/IAuthenticationService/GetPasswordRSAPublicKey/v1/",
                 params={"account_name": steam_login}, timeout=15)
    r.raise_for_status()
    rsa_resp = (r.json() or {}).get("response", {})
    mod, exp, ts = rsa_resp.get("publickey_mod"), rsa_resp.get("publickey_exp"), rsa_resp.get("timestamp")
    if not (mod and exp and ts):
        logger.error(f"_steam_login: нет RSA-ключа для {steam_login}")
        return None
    encrypted_password = _rsa_encrypt_password(password, mod, exp)

    # 2) Начало сессии авторизации с учётными данными
    r = sess.post(f"{STEAM_API_BASE}/IAuthenticationService/UpdateAuthSessionWithCredentials/v1/",
                  data={"account_name": steam_login,
                        "encrypted_password": encrypted_password,
                        "encryption_timestamp": ts,
                        "remember_login": "true"},
                  headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
    r.raise_for_status()
    data = (r.json() or {}).get("response", {})
    client_id = data.get("client_id")
    request_id = data.get("request_id")
    steamid = data.get("steamid")
    if not (client_id and request_id):
        logger.error(f"_steam_login: не удалось начать auth-сессию для {steam_login}: {data}")
        return None

    # 3) Steam Guard (TOTP из shared_secret)
    code = SharedSecretGuard(shared_secret).generate_code()
    if not code:
        logger.error(f"_steam_login: не сгенерировать Steam Guard код для {steam_login}")
        return None
    r = sess.post(f"{STEAM_API_BASE}/IAuthenticationService/UpdateAuthSessionWithMobileResponse/v1/",
                  data={"client_id": client_id, "steamid": steamid,
                        "code": code, "code_type": "3"},
                  headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
    r.raise_for_status()

    # 4) Poll — получаем refresh_token / access_token
    r = sess.post(f"{STEAM_API_BASE}/IAuthenticationService/PollAuthSessionStatus/v1/",
                  data={"client_id": client_id, "request_id": request_id},
                  headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
    r.raise_for_status()
    poll = (r.json() or {}).get("response", {})
    refresh_token = poll.get("refresh_token")
    if not refresh_token:
        logger.error(f"_steam_login: не получен refresh_token для {steam_login}: {poll}")
        return None

    sess.cookies.set("steamLoginSecure", refresh_token, domain="steamcommunity.com")
    sess.cookies.set("steamLoginSecure", refresh_token, domain="api.steampowered.com")
    logger.info(f"_steam_login: вход выполнен для {steam_login}")
    return sess


def _revoke_other_sessions(steam_login: str, sess) -> bool:
    """
    Отзыв всех других сессий. НЕПРОВЕРЕНО — точный эндпоинт Steam меняется.
    Возвращает True при успехе.
    """
    try:
        # Известный web-эндпоинт «deauthorize all other devices».
        # Если Steam изменит его — потребуется корректировка после теста.
        r = sess.post("https://steamcommunity.com/login/deauthorize/",
                      data={"steamid": "", "deauthorizesn": "1"},
                      headers={"Content-Type": "application/x-www-form-urlencoded"},
                      timeout=15)
        ok = r.status_code == 200
        logger.info(f"_revoke_other_sessions({steam_login}): status={r.status_code} ok={ok}")
        return ok
    except Exception as exc:
        logger.error(f"_revoke_other_sessions({steam_login}): {exc}")
        return False


def kick_live(steam_login: str, password: str, shared_secret: str,
              method: str = "revoke_sessions") -> dict:
    """
    Реальный «кик» через Steam-протокол. НЕПРОВЕРЕНО.

    :param method: "revoke_sessions" — отозвать другие сессии (мягче);
                   "change_password" — сменить пароль (надёжно выкинет всех,
                   но Steam кулдаунит частую смену и пароль в товаре «протухнет»).
    """
    if not ENABLE_UNVERIFIED_STEAM_KICK:
        logger.warning("kick_live: live-кик отключён (ENABLE_UNVERIFIED_STEAM_KICK=False). "
                       "Включайте только после теста на burner-аккаунте.")
        return {"ok": False, "action": "disabled", "reason": "unverified_kick_disabled"}

    try:
        sess = _steam_login(steam_login, password, shared_secret)
        if sess is None:
            return {"ok": False, "action": method, "error": "login_failed"}

        if method == "revoke_sessions":
            ok = _revoke_other_sessions(steam_login, sess)
            return {"ok": ok, "action": "revoke_sessions", "steam_login": steam_login}
        # change_password намеренно не реализован как деструктивный — требует
        # отдельной проработки и теста; вернём явный «не поддерживается».
        logger.warning("kick_live: change_password не реализован (деструктивно).")
        return {"ok": False, "action": "change_password", "error": "not_implemented"}
    except Exception as exc:
        logger.error(f"kick_live({steam_login}, {method}): {exc}")
        return {"ok": False, "action": method, "error": str(exc)}


# ---------------------------------------------------------------------------
# Точка входа, вызываемая из rental_callback / profile_guardian.
# ---------------------------------------------------------------------------
def kick_account(cardinal, steam_login: str, steam_password: str, shared_secret: str,
                 session_id: str, method: str = "notify", reason: str = "rental_expired") -> dict:
    """
    Безопасная обёртка. По умолчанию method="notify" — только уведомление владельца.

    :param method: "notify" (по умолчанию, безопасно) | "revoke_sessions" (live, непроверено)
    :param reason: "rental_expired" | "profile_tamper"
    """
    if not steam_login:
        logger.warning(f"kick_account: нет steam_login для сессии {session_id} — пропуск")
        return {"ok": False, "error": "no_steam_login"}

    if method == "notify" or not steam_password or not shared_secret:
        return kick_notify(cardinal, steam_login, session_id, reason)

    return kick_live(steam_login, steam_password, shared_secret, method=method)
