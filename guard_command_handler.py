#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Хендлер Guard-команд. Каждая команда привязана к своему Steam-аккаунту
через флаг в configs/shared_secrets.cfg.

  /code, !code, !код  → primary        (yura_yshakov)
  /guard              → codes_account  (meloed432)
  /login              → coode_account  (juroverload)
  /cs                 → cs_account     (cs_global_funpay)
  /rust               → rust_account   (nastyaonemylove)
  /ruster             → ruster_account (je_ruster)
"""

from __future__ import annotations
from typing import TYPE_CHECKING
import logging
import time

if TYPE_CHECKING:
    from cardinal import Cardinal

from FunPayAPI.updater.events import NewMessageEvent, LastChatMessageChangedEvent
from guard_handler import get_guard_handler
from purchase_checker import get_purchase_checker
from Utils import cardinal_tools
from threading import Thread

logger = logging.getLogger("GuardCommand")


# ---------------------------------------------------------------------------
# Хелперы резолва аккаунтов
# ---------------------------------------------------------------------------

def _resolve_account_by_flag(flag_name: str) -> str | None:
    import configparser as _cp
    try:
        _ss = _cp.ConfigParser()
        _ss.read("configs/shared_secrets.cfg", encoding="utf-8")
        found = []
        for sec in _ss.sections():
            if _ss[sec].get(flag_name, "").strip().lower() in ("1", "true", "yes", "on"):
                found.append(_ss[sec].get("username", sec))
        if not found:
            return None
        if len(found) > 1:
            logger.warning(f"Флаг {flag_name}: несколько аккаунтов {found}, используем первый")
        return found[0]
    except Exception as ex:
        logger.error(f"_resolve_account_by_flag({flag_name}): {ex}")
        return None


def _resolve_primary_account_username() -> str | None:
    import configparser as _cp
    try:
        _ss = _cp.ConfigParser()
        _ss.read("configs/shared_secrets.cfg", encoding="utf-8")
        for sec in _ss.sections():
            if _ss[sec].get("primary", "").strip().lower() in ("1", "true", "yes", "on"):
                return _ss[sec].get("username", sec)
        secs = _ss.sections()
        if len(secs) == 1:
            return _ss[secs[0]].get("username", secs[0])
    except Exception as ex:
        logger.error(f"_resolve_primary_account_username: {ex}")
    return None


def _resolve_codes_account() -> str | None:
    return _resolve_account_by_flag("codes_account")


def _resolve_coode_account() -> str | None:
    return _resolve_account_by_flag("coode_account")


def _resolve_cs_account() -> str | None:
    return _resolve_account_by_flag("cs_account")


def _resolve_rust_account() -> str | None:
    return _resolve_account_by_flag("rust_account")


def _resolve_ruster_account() -> str | None:
    return _resolve_account_by_flag("ruster_account")


# ---------------------------------------------------------------------------
# Общие вспомогательные функции
# ---------------------------------------------------------------------------

def _extract_event(c, e):
    """Возвращает (obj, mtext, chat_id, chat_name, username) или None."""
    if not c.autoresponse_enabled:
        return None
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return None
        if e.message.author == c.account.username:
            return None
        return e.message, str(e.message), e.message.chat_id, e.message.chat_name, e.message.author
    else:
        return e.chat, str(e.chat), e.chat.id, e.chat.name, e.chat.name


def _check_session_revoked(c, chat_id, chat_name) -> bool:
    """Возвращает True если сессия отозвана (и отправляет сообщение покупателю)."""
    try:
        from rental_system import get_rental_manager
        rm = get_rental_manager()
        if rm:
            session = rm.get_active_session_for_chat(chat_id)
            deny_msg = (
                "⏰ Ваша сессия завершена — доступ к коду Steam Guard закрыт.\n"
                "Для продолжения оформите новую покупку."
            )
            if session is None:
                for s in rm.sessions.values():
                    if str(s.user_id) == str(chat_id) and s.code_revoked:
                        Thread(target=c.send_message, args=(chat_id, deny_msg, chat_name), daemon=True).start()
                        return True
            elif session.code_revoked:
                Thread(target=c.send_message, args=(chat_id, deny_msg, chat_name), daemon=True).start()
                return True
    except Exception:
        pass
    return False


def _start_session_timer(chat_id: int, cmd: str):
    """Запускает таймер оффлайн/аренды при первом получении кода."""
    try:
        from rental_system import get_rental_manager
        rm = get_rental_manager()
        if rm:
            sess = rm.get_active_session_for_chat(chat_id)
            if sess and sess.is_offline and sess.offline_code_used_at is None:
                sess.mark_offline_code_used()
                rm.save_sessions()
            elif sess and sess.is_rental and sess.rental_code_used_at is None:
                sess.mark_rental_code_used()
                rm.save_sessions()
    except Exception as ex:
        logger.error(f"{cmd}: timer: {ex}")


def _notify_owner(c, username, chat_name, chat_id, cmd, account, code):
    """Отправляет уведомление владельцу в Telegram."""
    if not (c.telegram and c.telegram.bot and c.telegram.owner_chat_id):
        return
    try:
        msg = (
            f"🔔 Guard код ({cmd})\n\n"
            f"👤 {username}\n💬 {chat_name}\n"
            f"🔑 Аккаунт: {account}\n🔑 Код: {code}\n"
            f"🆔 Chat ID: {chat_id}"
        )
        Thread(target=c.telegram.bot.send_message,
               args=(c.telegram.owner_chat_id, msg),
               daemon=True).start()
    except Exception as ex:
        logger.error(f"_notify_owner({cmd}): {ex}")


def _handle_command(c, e, cmd: str, account_resolver):
    """
    Универсальный обработчик Guard-команды.
    cmd           — строка команды (например '/guard')
    account_resolver — функция возвращающая username аккаунта
    """
    result = _extract_event(c, e)
    if result is None:
        return
    obj, mtext, chat_id, chat_name, username = result

    if mtext.replace("\n", "").strip().lower() != cmd:
        return

    logger.info(f"Команда {cmd} от {username} в чате {chat_name}")

    pc = get_purchase_checker()
    if pc and _check_session_revoked(c, chat_id, chat_name):
        return

    account = account_resolver()
    if account:
        logger.info(f"{cmd}: аккаунт {account}")
    else:
        logger.warning(f"{cmd}: аккаунт не настроен")

    gh = get_guard_handler()
    code = None
    code_generated = False

    if gh and gh.enabled and account:
        try:
            code = gh.get_guard_code_for_account(account)
            if code and not code.startswith("❌"):
                _start_session_timer(chat_id, cmd)
                _remaining = 30 - (int(time.time()) % 30)
                response_text = (
                    f"🔑 Код Steam Guard: {code}\n"
                    f"⏱ Действителен ещё {_remaining} сек"
                )
                code_generated = True
            else:
                response_text = (
                    "⏳ Код не может быть сгенерирован.\n"
                    "Попробуйте снова или обратитесь к продавцу."
                )
        except Exception as ex:
            logger.error(f"Ошибка Guard кода ({cmd}): {ex}")
            response_text = "❌ Ошибка при получении кода. Попробуйте через 30 секунд."
    elif gh and gh.enabled and not account:
        response_text = f"❌ Команда {cmd} пока не настроена продавцом."
    else:
        response_text = "❌ Система Steam Guard не настроена."

    Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()

    if code_generated:
        _notify_owner(c, username, chat_name, chat_id, cmd, account, code)


# ---------------------------------------------------------------------------
# Команда /code + !code — аккаунт 1 (primary)
# ---------------------------------------------------------------------------

def handle_guard_code_command(c, e):
    """Команды /code и !code → основной аккаунт (primary)."""
    result = _extract_event(c, e)
    if result is None:
        return
    obj, mtext, chat_id, chat_name, username = result

    cmd_lower = mtext.replace("\n", "").strip().lower()
    if cmd_lower not in ("/code", "!code"):
        return

    logger.info(f"Команда /code от {username} в чате {chat_name}")

    pc = get_purchase_checker()
    if pc and _check_session_revoked(c, chat_id, chat_name):
        return

    primary_account = _resolve_primary_account_username()

    gh = get_guard_handler()
    code = None
    code_generated = False

    if gh and gh.enabled:
        try:
            if primary_account:
                code = gh.get_guard_code_for_account(primary_account)
            if code and not code.startswith("❌"):
                _start_session_timer(chat_id, "/code")
                _remaining = 30 - (int(time.time()) % 30)
                response_text = (
                    f"🔑 Код Steam Guard: {code}\n"
                    f"⏱ Действителен ещё {_remaining} сек"
                )
                code_generated = True
            else:
                if gh.use_shared_secret:
                    response_text = (
                        "⏳ Код не может быть сгенерирован (shared_secret).\n"
                        "Убедитесь, что аккаунт добавлен в систему.\n"
                        "Попробуйте снова или обратитесь к продавцу."
                    )
                else:
                    response_text = (
                        "⏳ Код ещё не пришёл на почту.\n"
                        "Попробуйте через 15–30 секунд — коды обновляются автоматически.\n"
                        "Если долго нет — напишите продавцу."
                    )
        except Exception as ex:
            logger.error(f"Ошибка Guard кода (/code): {ex}")
            response_text = "❌ Ошибка при получении кода. Попробуйте через 30 секунд."
    else:
        if "!code" in c.AR_CFG and c.AR_CFG["!code"].getboolean("enabled", fallback=True):
            response_text = cardinal_tools.format_msg_text(c.AR_CFG["!code"]["response"], obj)
        else:
            response_text = "❌ Система Steam Guard не настроена."

    Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()

    if code_generated:
        _notify_owner(c, username, chat_name, chat_id, "/code", primary_account or "?", code)


# ---------------------------------------------------------------------------
# Команда !код — аккаунт 1 (без проверки покупки)
# ---------------------------------------------------------------------------

BACKUP_COMMAND = "!код"


def handle_guard_code_backup_command(c, e):
    """Запасная команда !код → основной аккаунт, без проверок."""
    result = _extract_event(c, e)
    if result is None:
        return
    obj, mtext, chat_id, chat_name, username = result

    backup_kw = BACKUP_COMMAND.strip().lower()
    if not backup_kw or mtext.replace("\n", "").strip().lower() != backup_kw:
        return

    logger.info(f"Запасная команда {backup_kw} от {username} (без проверок)")

    primary_account = _resolve_primary_account_username()
    gh = get_guard_handler()
    code = None
    code_generated = False

    if gh and gh.enabled:
        try:
            if primary_account:
                code = gh.get_guard_code_for_account(primary_account)
            if code and not code.startswith("❌"):
                _start_session_timer(chat_id, backup_kw)
                _remaining = 30 - (int(time.time()) % 30)
                response_text = (
                    f"🔑 Код Steam Guard: <code>{code}</code>\n"
                    f"⏱ Действителен ещё {_remaining} сек"
                )
                code_generated = True
            else:
                response_text = (
                    "⏳ Код не может быть сгенерирован.\n"
                    "Убедитесь, что аккаунт добавлен в систему."
                )
        except Exception as ex:
            logger.error(f"Ошибка Guard кода ({backup_kw}): {ex}")
            response_text = "❌ Ошибка при получении кода. Попробуйте через 30 секунд."
    else:
        response_text = "❌ Система Steam Guard не настроена."

    Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()

    if code_generated:
        _notify_owner(c, username, chat_name, chat_id, backup_kw, primary_account or "?", code)


# ---------------------------------------------------------------------------
# Команды /guard /login /cs /rust /ruster — через универсальный обработчик
# ---------------------------------------------------------------------------

GUARD_COMMAND   = "/guard"
LOGIN_COMMAND   = "/login"
CS_COMMAND      = "/cs"
RUST_COMMAND    = "/rust"
RUSTER_COMMAND  = "/ruster"


def handle_guard_codes_command(c, e):
    _handle_command(c, e, GUARD_COMMAND, _resolve_codes_account)


def handle_guard_coode_command(c, e):
    _handle_command(c, e, LOGIN_COMMAND, _resolve_coode_account)


def handle_guard_cs_command(c, e):
    _handle_command(c, e, CS_COMMAND, _resolve_cs_account)


def handle_guard_rust_command(c, e):
    _handle_command(c, e, RUST_COMMAND, _resolve_rust_account)


def handle_guard_ruster_command(c, e):
    _handle_command(c, e, RUSTER_COMMAND, _resolve_ruster_account)


# ---------------------------------------------------------------------------
# Привязка к событиям
# Только /login (juroverload) отвечает покупателям в FunPay.
# Остальные команды доступны только через Telegram-бота владельцу.
# ---------------------------------------------------------------------------

BIND_TO_NEW_MESSAGE = [
    handle_guard_coode_command,
]
BIND_TO_LAST_CHAT_MESSAGE_CHANGED = [
    handle_guard_coode_command,
]
