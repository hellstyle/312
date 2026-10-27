#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
profile_guardian — фоновый монитор Steam-профиля арендованного аккаунта.

Если арендатор меняет настройки профиля (имя/аватар/приватность и т.п.) —
инициирует «кик» (через steam_kick.kick_account) и завершает сессию аренды.

ОГРАНИЧЕНИЯ (честно):
  * Для мониторинга нужен Steam Web API key (configs/_main.cfg [Steam] api_key).
    Сейчас он пуст — монитор будет работать только после его заполнения.
  * GetPlayerSummaries работает по steamid64. В товаре (storage/products/*.txt)
    сейчас только login:password, steamid там нет. Чтобы монитор работал, нужно
    указывать steamid аккаунта (поле RentalSession.steamid) — например, добавив
    его в файл товара в формате login:password:steamid, либо через конфиг.
    Без steamid монитор пропускает аккаунт с предупреждением.
  * Модуль по умолчанию ОТКЛЮЧЁН ([SteamGuard] profile_guard_enabled = 0).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cardinal import Cardinal

logger = logging.getLogger("ProfileGuardian")


def _get_player_summary(api_key: str, steamid: str, proxy=None) -> dict | None:
    try:
        import requests
        r = requests.get("https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
                         params={"key": api_key, "steamids": steamid}, timeout=15)
        r.raise_for_status()
        players = (r.json() or {}).get("response", {}).get("players", [])
        return players[0] if players else None
    except Exception as exc:
        logger.error(f"_get_player_summary({steamid}): {exc}")
        return None


def _snapshot(summary: dict) -> dict:
    """Берём из профиля поля, изменение которых подозрительно со стороны арендатора."""
    if not summary:
        return {}
    return {
        "personaname": summary.get("personaname"),
        "avatarhash": summary.get("avatarhash"),
        "profilestate": summary.get("profilestate"),
        "communityvisibilitystate": summary.get("communityvisibilitystate"),
        "profileurl": summary.get("profileurl"),
    }


class ProfileGuardian:
    """Фоновый мониторинг изменений профиля арендованных аккаунтов."""

    def __init__(self, cardinal: "Cardinal", api_key: str, check_interval: int = 300):
        self.cardinal = cardinal
        self.api_key = api_key
        self.check_interval = check_interval
        self.running = False
        self._thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        import threading
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(f"ProfileGuardian запущен (интервал {self.check_interval} сек)")

    def stop(self):
        self.running = False

    def capture_baseline(self, steamid: str) -> dict | None:
        """Снимает эталон профиля для сравнения (вызывается на старте аренды)."""
        if not self.api_key or not steamid:
            return None
        summary = _get_player_summary(self.api_key, steamid)
        snap = _snapshot(summary)
        return snap or None

    def _loop(self):
        while self.running:
            try:
                self._check_once()
            except Exception as e:
                logger.error(f"ProfileGuardian loop: {e}")
            time.sleep(self.check_interval)

    def _check_once(self):
        from rental_system import get_rental_manager
        from steam_kick import kick_account
        rm = get_rental_manager()
        if not rm:
            return

        for session in list(rm.sessions.values()):
            if not session.active:
                continue
            steamid = getattr(session, "steamid", None)
            baseline = getattr(session, "profile_baseline", None)
            if not steamid or not baseline:
                continue  # без steamid/эталона мониторить нечего

            snap = _snapshot(_get_player_summary(self.api_key, steamid))
            if not snap:
                continue
            if snap != baseline:
                logger.warning(
                    f"ProfileGuardian: профиль изменён у {session.steam_login} "
                    f"(сессия {session.session_id}) — инициирую кик")
                # обновим эталон, чтобы не кикать повторно
                session.profile_baseline = snap
                rm.save_sessions()
                cred = rm.get_credential(session.session_id)
                shared = self._shared_for(session.steam_login)
                kick_account(self.cardinal, session.steam_login,
                             cred or "", shared, session.session_id,
                             method="notify", reason="profile_tamper")
                rm.end_session(session.session_id)

    def _shared_for(self, steam_login: str) -> str:
        try:
            from shared_secret_guard import get_shared_secret_manager
            mgr = get_shared_secret_manager()
            if mgr and steam_login in mgr.accounts:
                # SharedSecretGuard хранит декодированный secret; вернём исходный base64,
                # который хранится в конфиге. Менеджер его не хранит, поэтому читаем из конфига.
                import configparser
                cfg = configparser.ConfigParser()
                cfg.read("configs/shared_secrets.cfg", encoding="utf-8")
                return cfg[steam_login].get("shared_secret", "") if steam_login in cfg.sections() else ""
        except Exception as e:
            logger.error(f"_shared_for({steam_login}): {e}")
        return ""


# Глобальный экземпляр
_profile_guardian: "ProfileGuardian" = None


def init_profile_guardian(cardinal: "Cardinal", api_key: str, check_interval: int = 300) -> "ProfileGuardian":
    global _profile_guardian
    _profile_guardian = ProfileGuardian(cardinal, api_key, check_interval)
    _profile_guardian.start()
    return _profile_guardian


def get_profile_guardian() -> "ProfileGuardian":
    return _profile_guardian
