#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
steam_kick — «выкинуть» арендатора из Steam-аккаунта по окончании аренды.

Метод кика — через refresh_token из shared_secrets.cfg:
  1. steam-cookies (pip install steam-cookies==0.2.0) получает steamLoginSecure cookie
     напрямую из refresh_token — без пароля, без Steam Auth API.
  2. Делает DeauthorizeRefreshTokens → все сессии арендатора завершаются.

Fallback: если refresh_token нет в конфиге или steam-cookies недоступен —
  только уведомление владельца (method="notify").
"""

from __future__ import annotations

import logging
import configparser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cardinal import Cardinal

logger = logging.getLogger("SteamKick")

ENABLE_UNVERIFIED_STEAM_KICK = True
STEAM_API_BASE = "https://api.steampowered.com"


# ---------------------------------------------------------------------------
# Безопасный метод — только уведомление владельца.
# ---------------------------------------------------------------------------

def kick_notify(cardinal: "Cardinal", steam_login: str, session_id: str,
                reason: str = "rental_expired") -> dict:
    """Уведомляет владельца бота в Telegram — реальных действий со Steam нет."""
    msg = (
        f"⛔ Аренда завершена — кикните аккаунт\n\n"
        f"👤 Аккаунт: <code>{steam_login}</code>\n"
        f"🆔 Сессия: {session_id}\n"
        f"📋 Причина: {reason}\n\n"
        f"Войдите в аккаунт и завершите все сессии\n"
        f"(Steam → Аккаунт → Безопасность → Sign out of all sessions)."
    )
    logger.info(f"kick_notify: {steam_login} ({reason}) — {session_id}")
    try:
        if cardinal is not None and getattr(cardinal, "telegram", None) and cardinal.telegram.bot:
            owner_chat_id = getattr(cardinal.telegram, "owner_chat_id", None)
            if owner_chat_id:
                import threading
                threading.Thread(
                    target=cardinal.telegram.bot.send_message,
                    args=(owner_chat_id, msg),
                    kwargs={"disable_notification": True},
                    daemon=True,
                ).start()
    except Exception as e:
        logger.error(f"kick_notify: {e}")
    return {"ok": True, "action": "notify", "steam_login": steam_login, "reason": reason}


# ---------------------------------------------------------------------------
# Получение refresh_token из shared_secrets.cfg
# ---------------------------------------------------------------------------

def _get_refresh_token(steam_login: str) -> str | None:
    """Читает refresh_token для аккаунта из configs/shared_secrets.cfg."""
    try:
        cfg = configparser.ConfigParser()
        cfg.read("configs/shared_secrets.cfg", encoding="utf-8")
        for sec in cfg.sections():
            if cfg[sec].get("username", sec) == steam_login:
                return cfg[sec].get("refresh_token", "").strip() or None
    except Exception as ex:
        logger.error(f"_get_refresh_token({steam_login}): {ex}")
    return None


# ---------------------------------------------------------------------------
# Вход через refresh_token (без пароля и Steam API)
# ---------------------------------------------------------------------------

def _steam_login_via_token(steam_login: str, refresh_token: str):
    """
    Получает steamLoginSecure cookie через refresh_token.
    Использует пакет steam-cookies — не требует доступа к api.steampowered.com.
    Возвращает requests.Session или None.
    """
    import asyncio
    try:
        import requests as _req
        from steam_cookies import SteamWebSessionClient

        async def _get():
            async with SteamWebSessionClient() as client:
                return await client.get_web_cookies(refresh_token)

        # Запускаем async в синхронном контексте
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    cookies = pool.submit(asyncio.run, _get()).result(timeout=30)
            else:
                cookies = loop.run_until_complete(_get())
        except RuntimeError:
            cookies = asyncio.run(_get())

        d = cookies.as_dict()
        secure = d.get("steamLoginSecure")
        if not secure:
            logger.error(f"_steam_login_via_token: нет steamLoginSecure для {steam_login}")
            return None

        sess = _req.Session()
        sess.headers.update({"User-Agent": "Mozilla/5.0 (steam_kick; FunPayCardinal)"})
        sess.cookies.set("steamLoginSecure", secure, domain="steamcommunity.com")
        if "sessionid" in d:
            sess.cookies.set("sessionid", d["sessionid"], domain="steamcommunity.com")
        logger.info(f"_steam_login_via_token: OK для {steam_login}")
        return sess

    except ImportError:
        logger.error("steam-cookies не установлен: pip install steam-cookies==0.2.0")
        return None
    except Exception as ex:
        logger.error(f"_steam_login_via_token({steam_login}): {ex}")
        return None


# ---------------------------------------------------------------------------
# Отзыв всех сессий
# ---------------------------------------------------------------------------

def _revoke_other_sessions(steam_login: str, sess, steamid: str = None) -> bool:
    """DeauthorizeRefreshTokens — завершает все активные сессии."""
    try:
        r = sess.post(
            f"{STEAM_API_BASE}/IAuthenticationService/DeauthorizeRefreshTokens/v1/",
            data={},
            timeout=15,
        )
        ok = r.status_code == 200
        logger.info(f"_revoke_other_sessions({steam_login}): HTTP {r.status_code} ok={ok}")
        if not ok:
            # Fallback — старый веб-эндпоинт steamcommunity
            r2 = sess.post(
                "https://steamcommunity.com/login/deauthorize/",
                data={"steamid": steamid or "", "deauthorizesn": "1"},
                timeout=15,
            )
            ok = r2.status_code == 200
            logger.info(f"_revoke_other_sessions fallback({steam_login}): HTTP {r2.status_code} ok={ok}")
        return ok
    except Exception as exc:
        logger.error(f"_revoke_other_sessions({steam_login}): {exc}")
        return False


# ---------------------------------------------------------------------------
# Основная точка входа для /kickall и rental_callback
# ---------------------------------------------------------------------------

def kick_live(steam_login: str, password: str, shared_secret: str,
              method: str = "revoke_sessions", steamid: str = None,
              refresh_token: str = None) -> dict:
    """
    Кик через refresh_token (предпочтительно) или через password + shared_secret.

    Приоритет:
      1. refresh_token из аргумента
      2. refresh_token из shared_secrets.cfg
      3. Логин через password + shared_secret (требует api.steampowered.com)
    """
    if not ENABLE_UNVERIFIED_STEAM_KICK:
        return {"ok": False, "action": "disabled", "reason": "unverified_kick_disabled"}

    # Пробуем получить refresh_token
    rt = refresh_token or _get_refresh_token(steam_login)

    sess = None

    if rt:
        logger.info(f"kick_live({steam_login}): используем refresh_token")
        sess = _steam_login_via_token(steam_login, rt)

    if sess is None:
        # Fallback — старый метод через password (требует API)
        logger.warning(f"kick_live({steam_login}): нет refresh_token или ошибка, пробуем password")
        if not password or not shared_secret:
            return {"ok": False, "action": method, "error": "no_credentials"}
        try:
            sess = _steam_login_password(steam_login, password, shared_secret)
        except Exception as ex:
            return {"ok": False, "action": method, "error": str(ex)}

    if sess is None:
        return {"ok": False, "action": method, "error": "login_failed"}

    ok = _revoke_other_sessions(steam_login, sess, steamid=steamid)
    return {"ok": ok, "action": "revoke_sessions", "steam_login": steam_login}


def _steam_login_password(steam_login: str, password: str, shared_secret: str):
    """
    Fallback: вход через password + shared_secret (BeginAuthSessionViaCredentials).
    Требует доступа к api.steampowered.com.
    """
    import base64
    import requests
    from shared_secret_guard import SharedSecretGuard

    API       = "https://api.steampowered.com"
    COMMUNITY = "https://steamcommunity.com"
    hdrs      = {"Referer": f"{COMMUNITY}/", "Origin": COMMUNITY}

    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (steam_kick; FunPayCardinal)"})
    try:
        sess.post(COMMUNITY, timeout=8)
    except Exception:
        pass

    # RSA ключ
    r = sess.get(f"{API}/IAuthenticationService/GetPasswordRSAPublicKey/v1/",
                 params={"account_name": steam_login}, headers=hdrs, timeout=12)
    r.raise_for_status()
    kd  = (r.json() or {}).get("response", {})
    mod, exp, ts = kd.get("publickey_mod"), kd.get("publickey_exp"), kd.get("timestamp")
    if not (mod and exp and ts):
        raise RuntimeError("нет RSA ключей")

    # Шифрование пароля
    try:
        from rsa import PublicKey, encrypt
        enc = base64.b64encode(encrypt(password.encode(), PublicKey(int(mod, 16), int(exp, 16)))).decode()
    except ImportError:
        from cryptography.hazmat.primitives.asymmetric import padding, rsa as _rsa
        from cryptography.hazmat.backends import default_backend
        pub = _rsa.RSAPublicNumbers(int(exp, 16), int(mod, 16)).public_key(default_backend())
        enc = base64.b64encode(pub.encrypt(password.encode(), padding.PKCS1v15())).decode()

    # Auth сессия
    r = sess.post(f"{API}/IAuthenticationService/BeginAuthSessionViaCredentials/v1/",
                  data={"persistence": "1", "encrypted_password": enc,
                        "account_name": steam_login, "encryption_timestamp": ts},
                  headers=hdrs, timeout=12)
    r.raise_for_status()
    resp = (r.json() or {}).get("response", {})
    cid, rid, sid = resp.get("client_id"), resp.get("request_id"), resp.get("steamid")
    if not (cid and rid):
        raise RuntimeError(f"нет client_id: {resp}")

    # Guard code
    code = SharedSecretGuard(shared_secret).generate_code()
    if not code:
        raise RuntimeError("не сгенерировать Guard код")

    r = sess.post(f"{API}/IAuthenticationService/UpdateAuthSessionWithSteamGuardCode/v1/",
                  data={"client_id": cid, "steamid": sid, "code": code, "code_type": "3"},
                  headers=hdrs, timeout=12)
    r.raise_for_status()

    # Poll
    r = sess.post(f"{API}/IAuthenticationService/PollAuthSessionStatus/v1/",
                  data={"client_id": cid, "request_id": rid}, headers=hdrs, timeout=12)
    r.raise_for_status()
    rt = (r.json() or {}).get("response", {}).get("refresh_token")
    if not rt:
        raise RuntimeError("нет refresh_token")

    # Финализация
    sessionid = sess.cookies.get("sessionid", "")
    r = sess.post(f"{COMMUNITY}/login/jwt/finalizelogin",
                  data={"nonce": rt, "sessionid": sessionid,
                        "redir": f"{COMMUNITY}/login/home/?goto="},
                  headers=hdrs, timeout=12)
    try:
        rj = r.json() or {}
        for item in (rj.get("transfer_info") or []):
            p = dict(item.get("params", {}))
            p["steamID"] = rj.get("steamID", sid)
            sess.post(item["url"], data=p, headers=hdrs, timeout=12)
    except Exception:
        pass

    logger.info(f"_steam_login_password: OK для {steam_login}")
    return sess


def kick_account(cardinal, steam_login: str, steam_password: str, shared_secret: str,
                 session_id: str, method: str = "notify", reason: str = "rental_expired",
                 steamid: str = None) -> dict:
    """
    Точка входа из rental_callback и profile_guardian.
    method="revoke_sessions" → реальный кик; method="notify" → только уведомление.
    """
    if not steam_login:
        logger.warning(f"kick_account: нет steam_login для {session_id}")
        return {"ok": False, "error": "no_steam_login"}

    if method == "notify":
        return kick_notify(cardinal, steam_login, session_id, reason)

    result = kick_live(steam_login, steam_password, shared_secret,
                       method=method, steamid=steamid)
    kick_notify(cardinal, steam_login, session_id,
                reason=f"{reason} (kick={'ok' if result.get('ok') else 'failed'})")
    return result
