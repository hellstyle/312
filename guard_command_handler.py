#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Хендлер для команды !code с интеграцией Gmail Guard.
Регистрируется через BIND_TO_NEW_MESSAGE / BIND_TO_LAST_CHAT_MESSAGE_CHANGED.
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


def handle_guard_code_command(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent):
    """Обработчик команды !code — выдаёт Steam Guard код через Gmail интеграцию."""
    if not c.autoresponse_enabled:
        return

    # Определяем режим работы
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return
        obj, mtext = e.message, str(e.message)
        chat_id, chat_name, username = e.message.chat_id, e.message.chat_name, e.message.author
    else:
        obj, mtext = e.chat, str(e.chat)
        chat_id, chat_name, username = obj.id, obj.name, obj.name

    # Пропускаем собственные сообщения
    if not c.old_mode_enabled and e.message.author == c.account.username:
        return

    # Реагируем только на команду /code (аккаунт 1 — основной)
    if mtext.replace("\n", "").strip().lower() != "/code":
        return

    logger.info(f"Команда /code от {username} в чате {chat_name}")

    attempts_line = ""

    # --- Проверяем только отозванный код (истечение аренды) — без проверки факта покупки ---
    pc = get_purchase_checker()
    if pc:
        try:
            from rental_system import get_rental_manager
            rm = get_rental_manager()
            if rm:
                session = rm.get_active_session_for_chat(chat_id)
                if session is None:
                    # Проверяем завершённые сессии с отозванным кодом
                    for s in rm.sessions.values():
                        if str(s.user_id) == str(chat_id) and s.code_revoked:
                            deny_msg = (
                                "⏰ Ваша сессия завершена — доступ к коду Steam Guard закрыт.\n"
                                "Для продолжения оформите новую покупку."
                            )
                            Thread(target=c.send_message, args=(chat_id, deny_msg, chat_name), daemon=True).start()
                            return
                elif session.code_revoked:
                    deny_msg = (
                        "⏰ Ваша сессия завершена — доступ к коду Steam Guard закрыт.\n"
                        "Для продолжения оформите новую покупку."
                    )
                    Thread(target=c.send_message, args=(chat_id, deny_msg, chat_name), daemon=True).start()
                    return
        except Exception:
            pass

    # --- Попытка получить Guard код через Guard систему (Gmail или shared_secret) ---
    gh = get_guard_handler()
    code_generated = False

    # !code работает СТРОГО с первым (основным) аккаунтом — секцией с primary = true
    # в shared_secrets.cfg. Привязка чата и lookup по нику НЕ используются, чтобы код
    # никогда не уходил со второго аккаунта (meloed432, для него есть команда /guard).
    primary_account = _resolve_primary_account_username()
    if primary_account:
        logger.info(f"!code: используется основной аккаунт {primary_account}")
    else:
        logger.warning("!code: основной аккаунт не найден (нет секции с primary = true)")

    if gh and gh.enabled:
        try:
            code = None
            if primary_account:
                code = gh.get_guard_code_for_account(primary_account)

            if code and not code.startswith("❌"):
                # ── Запускаем таймер оффлайн-сессии при первом !code ──────
                try:
                    from rental_system import get_rental_manager
                    rm = get_rental_manager()
                    if rm:
                        sess = rm.get_active_session_for_chat(chat_id)
                        if sess and sess.is_offline and sess.offline_code_used_at is None:
                            sess.mark_offline_code_used()
                            try:
                                rm.save_sessions()
                            except Exception as _se:
                                logger.error(f"!code: не сохранить offline timer: {_se}")
                        elif sess and sess.is_rental and sess.rental_code_used_at is None:
                            sess.mark_rental_code_used()
                            try:
                                rm.save_sessions()
                            except Exception as _se:
                                logger.error(f"!code: не сохранить rental timer: {_se}")
                except Exception:
                    pass
            if code and not code.startswith("❌"):
                _remaining = 30 - (int(time.time()) % 30)
                response_text = (
                    f"🔑 Код Steam Guard: {code}\n"
                    f"⏱ Действителен ещё {_remaining} сек{attempts_line}"
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
            logger.error(f"Ошибка получения Guard кода: {ex}")
            response_text = (
                "❌ Ошибка при получении кода Steam Guard. "
                "Попробуйте через 30 секунд или обратитесь к продавцу."
            )
    else:
        # Gmail не настроен — используем стандартный ответ из конфига auto_response
        if "!code" in c.AR_CFG and c.AR_CFG["!code"].getboolean("enabled", fallback=True):
            response_text = cardinal_tools.format_msg_text(c.AR_CFG["!code"]["response"], obj)
            response_text += attempts_line
        else:
            response_text = (
                "❌ Система Steam Guard не настроена. "
                "Обратитесь к продавцу для получения кода вручную."
            )

    Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()
    
    # --- Отправка уведомления владельцу бота при успешной генерации кода ---
    if code_generated and c.telegram and c.telegram.bot:
        try:
            owner_message = (
                f"🔔 Уведомление о Guard коде\n\n"
                f"👤 Пользователь: {username}\n"
                f"💬 Чат: {chat_name}\n"
                f"🔑 Код: {code}\n"
                f"⏱ Действителен ~30 секунд\n"
                f"🆔 Chat ID: {chat_id}"
            )
            
            # Отправляем владельцу бота если указан owner_chat_id
            if c.telegram.owner_chat_id:
                Thread(target=c.telegram.bot.send_message, 
                      args=(c.telegram.owner_chat_id, owner_message), 
                      daemon=True).start()
                logger.info(f"Уведомление о Guard коде отправлено владельцу: {username} -> {code}")
            else:
                # Если owner_chat_id не указан, отправляем в лог
                logger.info(f"GUARD CODE GENERATED: {username} -> {code}")
                
        except Exception as ex:
            logger.error(f"Ошибка отправки уведомления владельцу: {ex}")


# Запасная команда выдачи Guard-кода БЕЗ проверок purchase_checker
# (без требования покупки и без лимита попыток).
# Измените значение, чтобы задать своё ключевое слово (регистр не важен).
BACKUP_COMMAND = "!код"


def handle_guard_code_backup_command(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent):
    """Обработчик запасной команды (!код) — выдаёт Steam Guard код БЕЗ проверок
    purchase_checker (без требования покупки и без лимита попыток)."""
    if not c.autoresponse_enabled:
        return

    # Определяем режим работы
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return
        obj, mtext = e.message, str(e.message)
        chat_id, chat_name, username = e.message.chat_id, e.message.chat_name, e.message.author
    else:
        obj, mtext = e.chat, str(e.chat)
        chat_id, chat_name, username = obj.id, obj.name, obj.name

    # Пропускаем собственные сообщения
    if not c.old_mode_enabled and e.message.author == c.account.username:
        return

    backup_kw = BACKUP_COMMAND.strip().lower()
    if not backup_kw:
        return  # запасная команда отключена (пустое значение)
    if mtext.replace("\n", "").strip().lower() != backup_kw:
        return

    logger.info(f"Запасная команда {backup_kw} от {username} в чате {chat_name} (без проверок)")

    # !код работает СТРОГО с первым (основным) аккаунтом — секцией с primary = true
    # в shared_secrets.cfg. Привязка чата и lookup по нику НЕ используются
    # (проверок purchase_checker здесь и так нет, но аккаунт — всегда основной).
    primary_account = _resolve_primary_account_username()
    if primary_account:
        logger.info(f"{backup_kw}: используется основной аккаунт {primary_account}")
    else:
        logger.warning(f"{backup_kw}: основной аккаунт не найден (нет primary = true)")

    gh = get_guard_handler()
    code_generated = False
    code = None

    if gh and gh.enabled:
        try:
            if primary_account:
                code = gh.get_guard_code_for_account(primary_account)

            if code and not code.startswith("❌"):
                # ── Запускаем таймер оффлайн/аренды при первом запросе кода ──
                try:
                    from rental_system import get_rental_manager
                    rm = get_rental_manager()
                    if rm:
                        sess = rm.get_active_session_for_chat(chat_id)
                        if sess and sess.is_offline and sess.offline_code_used_at is None:
                            sess.mark_offline_code_used()
                            try:
                                rm.save_sessions()
                            except Exception as _se:
                                logger.error(f"{backup_kw}: offline timer: {_se}")
                        elif sess and sess.is_rental and sess.rental_code_used_at is None:
                            sess.mark_rental_code_used()
                            try:
                                rm.save_sessions()
                            except Exception as _se:
                                logger.error(f"{backup_kw}: rental timer: {_se}")
                except Exception:
                    pass
                _remaining = 30 - (int(time.time()) % 30)
                response_text = (
                    f"🔑 Код Steam Guard: <code>{code}</code>\n"
                    f"⏱ Действителен ещё {_remaining} сек"
                )
                code_generated = True
            else:
                if gh.use_shared_secret:
                    response_text = (
                        "⏳ Код не может быть сгенерирован (shared_secret).\n"
                        "Убедитесь, что аккаунт добавлен в систему."
                    )
                else:
                    response_text = (
                        "⏳ Код ещё не пришёл на почту.\n"
                        "Попробуйте через 15–30 секунд — коды обновляются автоматически."
                    )
        except Exception as ex:
            logger.error(f"Ошибка получения Guard кода (запасная команда): {ex}")
            response_text = (
                "❌ Ошибка при получении кода Steam Guard. "
                "Попробуйте через 30 секунд или обратитесь к продавцу."
            )
    else:
        response_text = (
            "❌ Система Steam Guard не настроена. "
            "Обратитесь к продавцу для получения кода вручную."
        )

    Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()

    # --- Отправка уведомления владельцу (видно, что код выдан без проверок) ---
    if code_generated and c.telegram and c.telegram.bot:
        try:
            owner_message = (
                f"🔔 Guard код (запасная команда, без проверок)\n\n"
                f"👤 Пользователь: {username}\n"
                f"💬 Чат: {chat_name}\n"
                f"🔑 Код: {code}\n"
                f"⏱ Действителен ~30 секунд\n"
                f"🆔 Chat ID: {chat_id}"
            )
            if c.telegram.owner_chat_id:
                Thread(target=c.telegram.bot.send_message,
                       args=(c.telegram.owner_chat_id, owner_message),
                       daemon=True).start()
                logger.info(f"Запасная команда: уведомление владельцу: {username} -> {code}")
            else:
                logger.info(f"BACKUP GUARD CODE: {username} -> {code} (Chat ID: {chat_id})")
        except Exception as ex:
            logger.error(f"Ошибка отправки уведомления владельцу (запасная команда): {ex}")


# ---------------------------------------------------------------------------
# Команда /guard — Guard код с ДРУГОГО аккаунта (не основного).
# Аккаунт выбирается по флагу codes_account = true в секции shared_secrets.cfg.
# Позволяет для отдельных товаров выдавать код от второго Steam-аккаунта,
# не привязываясь к названию лота: просто скажите покупателю писать /guard.
# ---------------------------------------------------------------------------
GUARD_COMMAND = "/guard"


def _resolve_account_by_flag(flag_name: str) -> str | None:
    """
    Возвращает username первого аккаунта, у которого в секции shared_secrets.cfg
    установлен флаг <flag_name> = true. Возвращается именно `username` — ключ,
    по которому SharedSecretManager хранит аккаунты. Если ни одна секция не
    отмечена — возвращает None.

    Используется для команд-«переключателей» аккаунтов: /guard (codes_account),
    /login (coode_account) и т.п.
    """
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
            logger.warning(f"Флаг {flag_name}: отмечено несколько аккаунтов ({found}), используется первый: {found[0]}")
        return found[0]
    except Exception as ex:
        logger.error(f"_resolve_account_by_flag({flag_name}): {ex}")
        return None


def _resolve_codes_account() -> str | None:
    """Аккаунт для команды /guard (флаг codes_account = true в shared_secrets.cfg)."""
    return _resolve_account_by_flag("codes_account")


def _resolve_coode_account() -> str | None:
    """Аккаунт для команды /login (флаг coode_account = true в shared_secrets.cfg)."""
    return _resolve_account_by_flag("coode_account")


def _resolve_primary_account_username() -> str | None:
    """
    Возвращает username ОСНОВНОГО Guard-аккаунта (поле `primary = true` в
    shared_secrets.cfg). Возвращается именно `username` — ключ, по которому
    SharedSecretManager хранит аккаунты.

    Используется командами !code и !код, чтобы они ВСЕГДА работали строго
    с первым аккаунтом и никогда не отдавали код со второго (для второго
    есть отдельная команда /guard).
    """
    import configparser as _cp
    try:
        _ss = _cp.ConfigParser()
        _ss.read("configs/shared_secrets.cfg", encoding="utf-8")
        for sec in _ss.sections():
            if _ss[sec].get("primary", "").strip().lower() in ("1", "true", "yes", "on"):
                return _ss[sec].get("username", sec)
        # Если ни одна секция не отмечена primary — для одиночного аккаунта
        # возвращаем его (чтобы !code продолжал работать в старой конфигурации).
        secs = _ss.sections()
        if len(secs) == 1:
            return _ss[secs[0]].get("username", secs[0])
    except Exception as ex:
        logger.error(f"_resolve_primary_account_username: {ex}")
    return None


def handle_guard_codes_command(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent):
    """Обработчик команды /guard — выдаёт Steam Guard код с ДРУГОГО аккаунта
    (отмеченного в shared_secrets.cfg флагом codes_account = true)."""
    if not c.autoresponse_enabled:
        return

    # Определяем режим работы
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return
        obj, mtext = e.message, str(e.message)
        chat_id, chat_name, username = e.message.chat_id, e.message.chat_name, e.message.author
    else:
        obj, mtext = e.chat, str(e.chat)
        chat_id, chat_name, username = obj.id, obj.name, obj.name

    # Пропускаем собственные сообщения
    if not c.old_mode_enabled and e.message.author == c.account.username:
        return

    # Реагируем только на команду /guard (точное совпадение, !code сюда не попадает)
    if mtext.replace("\n", "").strip().lower() != GUARD_COMMAND:
        return

    logger.info(f"Команда /guard от {username} в чате {chat_name}")

    attempts_line = ""

    # --- Проверяем отозванный код (истечение аренды) — как в !code ---
    pc = get_purchase_checker()
    if pc:
        try:
            from rental_system import get_rental_manager
            rm = get_rental_manager()
            if rm:
                session = rm.get_active_session_for_chat(chat_id)
                if session is None:
                    for s in rm.sessions.values():
                        if str(s.user_id) == str(chat_id) and s.code_revoked:
                            deny_msg = (
                                "⏰ Ваша сессия завершена — доступ к коду Steam Guard закрыт.\n"
                                "Для продолжения оформите новую покупку."
                            )
                            Thread(target=c.send_message, args=(chat_id, deny_msg, chat_name), daemon=True).start()
                            return
                elif session.code_revoked:
                    deny_msg = (
                        "⏰ Ваша сессия завершена — доступ к коду Steam Guard закрыт.\n"
                        "Для продолжения оформите новую покупку."
                    )
                    Thread(target=c.send_message, args=(chat_id, deny_msg, chat_name), daemon=True).start()
                    return
        except Exception:
            pass

    # --- Определяем аккаунт для /guard по флагу codes_account в shared_secrets.cfg ---
    codes_account = _resolve_codes_account()
    if codes_account:
        logger.info(f"/guard: используется аккаунт {codes_account}")
    else:
        logger.warning("/guard: аккаунт не настроен — нет секции с codes_account = true в shared_secrets.cfg")

    gh = get_guard_handler()
    code_generated = False
    code = None

    if gh and gh.enabled and codes_account:
        try:
            code = gh.get_guard_code_for_account(codes_account)

            if code and not code.startswith("❌"):
                # ── Запускаем таймер оффлайн/аренды при первом /guard ─────
                try:
                    from rental_system import get_rental_manager
                    rm = get_rental_manager()
                    if rm:
                        sess = rm.get_active_session_for_chat(chat_id)
                        if sess and sess.is_offline and sess.offline_code_used_at is None:
                            sess.mark_offline_code_used()
                            try:
                                rm.save_sessions()
                            except Exception as _se:
                                logger.error(f"/guard: не сохранить offline timer: {_se}")
                        elif sess and sess.is_rental and sess.rental_code_used_at is None:
                            sess.mark_rental_code_used()
                            try:
                                rm.save_sessions()
                            except Exception as _se:
                                logger.error(f"/guard: не сохранить rental timer: {_se}")
                except Exception:
                    pass

                _remaining = 30 - (int(time.time()) % 30)
                response_text = (
                    f"🔑 Код Steam Guard: {code}\n"
                    f"⏱ Действителен ещё {_remaining} сек{attempts_line}"
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
            logger.error(f"Ошибка получения Guard кода (/guard): {ex}")
            response_text = (
                "❌ Ошибка при получении кода Steam Guard. "
                "Попробуйте через 30 секунд или обратитесь к продавцу."
            )
    elif gh and gh.enabled and not codes_account:
        # Guard включён, но аккаунт для /guard не отмечен в конфиге
        response_text = (
            "❌ Команда /guard пока не настроена продавцом. "
            "Обратитесь к продавцу для получения кода вручную."
        )
    else:
        response_text = (
            "❌ Система Steam Guard не настроена. "
            "Обратитесь к продавцу для получения кода вручную."
        )

    Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()

    # --- Уведомление владельцу бота при успешной генерации кода ---
    if code_generated and c.telegram and c.telegram.bot:
        try:
            owner_message = (
                f"🔔 Уведомление о Guard коде (/guard — другой аккаунт)\n\n"
                f"👤 Пользователь: {username}\n"
                f"💬 Чат: {chat_name}\n"
                f"🔑 Аккаунт: {codes_account}\n"
                f"🔑 Код: {code}\n"
                f"⏱ Действителен ~30 секунд\n"
                f"🆔 Chat ID: {chat_id}"
            )
            if c.telegram.owner_chat_id:
                Thread(target=c.telegram.bot.send_message,
                       args=(c.telegram.owner_chat_id, owner_message),
                       daemon=True).start()
                logger.info(f"/guard: уведомление владельцу: {username} -> {code} (аккаунт {codes_account})")
            else:
                logger.info(f"CODES GUARD CODE: {username} -> {code} (аккаунт {codes_account}, Chat ID: {chat_id})")
        except Exception as ex:
            logger.error(f"Ошибка отправки уведомления владельцу (/guard): {ex}")


# ---------------------------------------------------------------------------
# Команда /login — Guard код с ТРЕТЬЕГО аккаунта.
# Аккаунт выбирается по флагу coode_account = true в секции shared_secrets.cfg.
# ---------------------------------------------------------------------------
LOGIN_COMMAND = "/login"


def handle_guard_coode_command(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent):
    """Обработчик команды /login — выдаёт Steam Guard код с ТРЕТЬЕГО аккаунта
    (отмеченного в shared_secrets.cfg флагом coode_account = true)."""
    if not c.autoresponse_enabled:
        return

    # Определяем режим работы
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return
        obj, mtext = e.message, str(e.message)
        chat_id, chat_name, username = e.message.chat_id, e.message.chat_name, e.message.author
    else:
        obj, mtext = e.chat, str(e.chat)
        chat_id, chat_name, username = obj.id, obj.name, obj.name

    # Пропускаем собственные сообщения
    if not c.old_mode_enabled and e.message.author == c.account.username:
        return

    # Реагируем только на команду /login (точное совпадение, !code//guard сюда не попадают)
    if mtext.replace("\n", "").strip().lower() != LOGIN_COMMAND:
        return

    logger.info(f"Команда /login от {username} в чате {chat_name}")

    attempts_line = ""

    # --- Проверяем отозванный код (истечение аренды) — как в !code ---
    pc = get_purchase_checker()
    if pc:
        try:
            from rental_system import get_rental_manager
            rm = get_rental_manager()
            if rm:
                session = rm.get_active_session_for_chat(chat_id)
                if session is None:
                    for s in rm.sessions.values():
                        if str(s.user_id) == str(chat_id) and s.code_revoked:
                            deny_msg = (
                                "⏰ Ваша сессия завершена — доступ к коду Steam Guard закрыт.\n"
                                "Для продолжения оформите новую покупку."
                            )
                            Thread(target=c.send_message, args=(chat_id, deny_msg, chat_name), daemon=True).start()
                            return
                elif session.code_revoked:
                    deny_msg = (
                        "⏰ Ваша сессия завершена — доступ к коду Steam Guard закрыт.\n"
                        "Для продолжения оформите новую покупку."
                    )
                    Thread(target=c.send_message, args=(chat_id, deny_msg, chat_name), daemon=True).start()
                    return
        except Exception:
            pass

    # --- Определяем аккаунт для /login по флагу coode_account в shared_secrets.cfg ---
    coode_account = _resolve_coode_account()
    if coode_account:
        logger.info(f"/login: используется аккаунт {coode_account}")
    else:
        logger.warning("/login: аккаунт не настроен — нет секции с coode_account = true в shared_secrets.cfg")

    gh = get_guard_handler()
    code_generated = False
    code = None

    if gh and gh.enabled and coode_account:
        try:
            code = gh.get_guard_code_for_account(coode_account)

            if code and not code.startswith("❌"):
                # ── Запускаем таймер оффлайн/аренды при первом /login ─────
                try:
                    from rental_system import get_rental_manager
                    rm = get_rental_manager()
                    if rm:
                        sess = rm.get_active_session_for_chat(chat_id)
                        if sess and sess.is_offline and sess.offline_code_used_at is None:
                            sess.mark_offline_code_used()
                            try:
                                rm.save_sessions()
                            except Exception as _se:
                                logger.error(f"/login: не сохранить offline timer: {_se}")
                        elif sess and sess.is_rental and sess.rental_code_used_at is None:
                            sess.mark_rental_code_used()
                            try:
                                rm.save_sessions()
                            except Exception as _se:
                                logger.error(f"/login: не сохранить rental timer: {_se}")
                except Exception:
                    pass

                _remaining = 30 - (int(time.time()) % 30)
                response_text = (
                    f"🔑 Код Steam Guard: {code}\n"
                    f"⏱ Действителен ещё {_remaining} сек{attempts_line}"
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
            logger.error(f"Ошибка получения Guard кода (/login): {ex}")
            response_text = (
                "❌ Ошибка при получении кода Steam Guard. "
                "Попробуйте через 30 секунд или обратитесь к продавцу."
            )
    elif gh and gh.enabled and not coode_account:
        # Guard включён, но аккаунт для /login не отмечен в конфиге
        response_text = (
            "❌ Команда /login пока не настроена продавцом. "
            "Обратитесь к продавцу для получения кода вручную."
        )
    else:
        response_text = (
            "❌ Система Steam Guard не настроена. "
            "Обратитесь к продавцу для получения кода вручную."
        )

    Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()

    # --- Уведомление владельцу бота при успешной генерации кода ---
    if code_generated and c.telegram and c.telegram.bot:
        try:
            owner_message = (
                f"🔔 Уведомление о Guard коде (/login — другой аккаунт)\n\n"
                f"👤 Пользователь: {username}\n"
                f"💬 Чат: {chat_name}\n"
                f"🔑 Аккаунт: {coode_account}\n"
                f"🔑 Код: {code}\n"
                f"⏱ Действителен ~30 секунд\n"
                f"🆔 Chat ID: {chat_id}"
            )
            if c.telegram.owner_chat_id:
                Thread(target=c.telegram.bot.send_message,
                       args=(c.telegram.owner_chat_id, owner_message),
                       daemon=True).start()
                logger.info(f"/login: уведомление владельцу: {username} -> {code} (аккаунт {coode_account})")
            else:
                logger.info(f"COODE GUARD CODE: {username} -> {code} (аккаунт {coode_account}, Chat ID: {chat_id})")
        except Exception as ex:
            logger.error(f"Ошибка отправки уведомления владельцу (/login): {ex}")


# Привязываем хендлеры к событиям новых сообщений
BIND_TO_NEW_MESSAGE = [handle_guard_code_command, handle_guard_code_backup_command, handle_guard_codes_command, handle_guard_coode_command]
BIND_TO_LAST_CHAT_MESSAGE_CHANGED = [handle_guard_code_command, handle_guard_code_backup_command, handle_guard_codes_command, handle_guard_coode_command]
