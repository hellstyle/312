"""
Telegram-диалог для управления арендой.

Возможности:
1. При создании новой сессии аренды — бот спрашивает в Telegram сколько часов аренда
   (инлайн-кнопки 1, 2, 3, 6, 12, 24 ч + ввод вручную).
2. Предупреждение покупателю за N минут до окончания аренды.
3. Кнопки продления из уведомления в Telegram при истечении.
"""

from __future__ import annotations

import logging
import time
from threading import Thread
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cardinal import Cardinal

from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B

from tg_bot import CBT

logger = logging.getLogger("RentalTG")

# Время до окончания аренды для предупреждений (в секундах)
WARN_BEFORE_SECS = 15 * 60   # 15 минут — предупреждение №2 (финальное)
WARN_30_SECS     = 30 * 60   # 30 минут — предупреждение №1


def _rental_hours_keyboard(session_id: str) -> K:
    """Клавиатура выбора часов аренды."""
    kb = K(row_width=4)
    hours_options = [1, 2, 3, 6, 12, 24]
    buttons = [
        B(f"{h} ч", callback_data=f"{CBT.RENTAL_SET_HOURS}:{session_id}:{h}")
        for h in hours_options
    ]
    kb.add(*buttons)
    return kb


def _extend_keyboard(chat_id: int) -> K:
    """Клавиатура для продления после истечения."""
    kb = K(row_width=3)
    options = [1, 2, 3]
    buttons = [
        B(f"+{h} ч", callback_data=f"{CBT.RENTAL_EXPIRE_EXTEND}:{chat_id}:{h}")
        for h in options
    ]
    kb.add(*buttons)
    return kb


def send_rental_hours_question(cardinal: "Cardinal", session_id: str, buyer: str,
                                game_name: str, chat_id: int):
    """
    Отправляет в Telegram владельцу вопрос: сколько часов аренда для этого заказа.
    Вызывается сразу после создания сессии.
    """
    if not cardinal.telegram or not cardinal.telegram.bot:
        return
    owner_id = getattr(cardinal.telegram, "owner_chat_id", None)
    if not owner_id:
        return

    text = (
        f"⏱ <b>Новая аренда!</b>\n\n"
        f"👤 Покупатель: <b>{buyer}</b>\n"
        f"🎮 Игра: <b>{game_name}</b>\n"
        f"🆔 Сессия: <code>{session_id}</code>\n\n"
        f"<b>Сколько часов аренда?</b>\n"
        f"Нажми кнопку или введи ответным сообщением: <code>аренда {session_id} &lt;часы&gt;</code>"
    )
    kb = _rental_hours_keyboard(session_id)

    def _send():
        try:
            cardinal.telegram.bot.send_message(owner_id, text, reply_markup=kb)
        except Exception as e:
            logger.error(f"send_rental_hours_question: {e}")

    Thread(target=_send, daemon=True).start()


def send_rental_warning(cardinal: "Cardinal", session_id: str, buyer_chat_id: int,
                         game_name: str, remaining_secs: int):
    """
    Отправляет покупателю предупреждение о скором истечении аренды.
    """
    mins = remaining_secs // 60
    if mins >= 25:
        # Предупреждение за ~30 минут
        msg = (
            f"⏰ До окончания аренды осталось {mins} минут.\n"
            f"Игра будет недоступна через {mins} мин."
        )
    else:
        # Финальное предупреждение за ~15 минут
        msg = (
            f"🚨 До окончания аренды осталось {mins} минут!\n"
            f"Скоро доступ к коду Steam Guard будет закрыт.\n"
            f"Завершите игровую сессию или обратитесь к продавцу."
        )
    try:
        cardinal.send_message(buyer_chat_id, msg, watermark=False)
        logger.info(f"Предупреждение ({mins} мин) отправлено в чат {buyer_chat_id}")
    except Exception as e:
        logger.error(f"send_rental_warning: {e}")


def register_rental_tg_handlers(cardinal: "Cardinal"):
    """
    Регистрирует callback-хендлеры Telegram для управления арендой.
    Вызывается из cardinal.init() после инициализации Telegram-бота.
    """
    if not cardinal.telegram or not cardinal.telegram.bot:
        logger.warning("register_rental_tg_handlers: Telegram не инициализирован")
        return

    bot = cardinal.telegram.bot

    @bot.callback_query_handler(func=lambda call: call.data.startswith(f"{CBT.RENTAL_SET_HOURS}:"))
    def on_rental_set_hours(call):
        """Обработчик нажатия кнопки часов аренды."""
        try:
            _, session_id, hours_str = call.data.split(":", 2)
            hours = int(hours_str)
        except Exception:
            bot.answer_callback_query(call.id, "Ошибка данных")
            return

        try:
            from rental_system import get_rental_manager
            rm = get_rental_manager()
            if rm is None:
                bot.answer_callback_query(call.id, "❌ Rental manager не инициализирован")
                return

            session = rm.sessions.get(session_id)
            if session is None:
                bot.answer_callback_query(call.id, "❌ Сессия не найдена")
                return

            # Обновляем длительность
            old_hours = session.duration_hours
            session.duration_hours = hours
            session.end_time = session.start_time + hours * 3600
            rm.save_sessions()

            bot.answer_callback_query(call.id, f"✅ Установлено {hours} ч")
            bot.edit_message_text(
                f"✅ Аренда <b>{session.game_name}</b> — <b>{hours} ч</b> установлено!\n"
                f"👤 {session.user_id} | Сессия: <code>{session_id}</code>",
                call.message.chat.id,
                call.message.message_id,
            )

            # Сообщаем покупателю новую длительность
            try:
                buyer_chat_id = int(session.user_id)
                msg = (
                    f"✅ Аренда <b>{session.game_name}</b> подтверждена!\n"
                    f"⏱ Длительность: <b>{hours} ч</b> с момента скачивания игры."
                )
                cardinal.send_message(buyer_chat_id, msg, watermark=False)
            except Exception as ex:
                logger.error(f"on_rental_set_hours: не смог уведомить покупателя: {ex}")

            logger.info(f"Аренда {session_id}: часы обновлены {old_hours} → {hours}")
        except Exception as ex:
            logger.error(f"on_rental_set_hours: {ex}", exc_info=True)
            bot.answer_callback_query(call.id, "❌ Ошибка")

    @bot.callback_query_handler(func=lambda call: call.data.startswith(f"{CBT.RENTAL_EXPIRE_EXTEND}:"))
    def on_rental_expire_extend(call):
        """Обработчик продления аренды после истечения."""
        try:
            _, chat_id_str, hours_str = call.data.split(":", 2)
            buyer_chat_id = int(chat_id_str)
            hours = int(hours_str)
        except Exception:
            bot.answer_callback_query(call.id, "Ошибка данных")
            return

        try:
            from rental_system import get_rental_manager
            rm = get_rental_manager()
            if rm is None:
                bot.answer_callback_query(call.id, "❌ Rental manager недоступен")
                return

            # Ищем последнюю сессию этого покупателя
            session = None
            for s in reversed(list(rm.sessions.values())):
                if str(s.user_id) == str(buyer_chat_id):
                    session = s
                    break

            if session is None:
                bot.answer_callback_query(call.id, "❌ Сессия не найдена")
                return

            # Продлеваем: сбрасываем code_revoked и обновляем end_time
            session.code_revoked = False
            session.active = True
            session.end_time = time.time() + hours * 3600
            session.duration_hours += hours
            session.notification_sent = False  # сбрасываем флаг предупреждения
            session.warning_30_sent = False    # сбрасываем флаг 30-минутного предупреждения
            rm.save_sessions()

            bot.answer_callback_query(call.id, f"✅ Продлено на {hours} ч")
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)

            # Уведомляем покупателя
            try:
                msg = (
                    f"🎮 Аренда <b>{session.game_name}</b> продлена на <b>{hours} ч</b>!\n"
                    f"⏱ Доступ к коду Steam Guard восстановлен."
                )
                cardinal.send_message(buyer_chat_id, msg, watermark=False)
            except Exception as ex:
                logger.error(f"on_rental_expire_extend: уведомление покупателю: {ex}")

            logger.info(f"Аренда продлена для chat {buyer_chat_id} на {hours} ч")
        except Exception as ex:
            logger.error(f"on_rental_expire_extend: {ex}", exc_info=True)
            bot.answer_callback_query(call.id, "❌ Ошибка")

    logger.info("Rental TG хендлеры зарегистрированы")


def patch_rental_checker_with_warning(cardinal: "Cardinal"):
    """
    Патчит RentalManager чтобы отправлять предупреждение за WARN_BEFORE_SECS секунд
    до истечения аренды.
    Вызывать ПОСЛЕ init_rental_manager().
    """
    try:
        from rental_system import get_rental_manager
        rm = get_rental_manager()
        if rm is None:
            return

        original_check = rm._check_expired_sessions

        def _patched_check(interval, callback):
            import time as _t
            while rm.running:
                try:
                    now = _t.time()
                    # --- Предупреждения за N минут ---
                    with rm._lock if hasattr(rm, '_lock') else _noop_ctx():
                        sessions_snap = list(rm.sessions.values())

                    for sess in sessions_snap:
                        if not sess.active or sess.code_revoked:
                            continue
                        remaining = sess.get_remaining_time()

                        # Предупреждение №1 — за 30 минут
                        if (not getattr(sess, 'warning_30_sent', False)
                                and WARN_30_SECS >= remaining > WARN_BEFORE_SECS
                                and not sess.is_expired()):
                            sess.warning_30_sent = True
                            try:
                                rm.save_sessions()
                            except Exception:
                                pass
                            buyer_cid = int(sess.user_id)
                            Thread(
                                target=send_rental_warning,
                                args=(cardinal, sess.session_id, buyer_cid,
                                      sess.game_name, remaining),
                                daemon=True,
                            ).start()

                        # Предупреждение №2 — за 15 минут (финальное)
                        if (not sess.notification_sent
                                and 0 < remaining <= WARN_BEFORE_SECS
                                and not sess.is_expired()):
                            sess.notification_sent = True
                            try:
                                rm.save_sessions()
                            except Exception:
                                pass
                            buyer_cid = int(sess.user_id)
                            Thread(
                                target=send_rental_warning,
                                args=(cardinal, sess.session_id, buyer_cid,
                                      sess.game_name, remaining),
                                daemon=True,
                            ).start()

                    # --- Истёкшие сессии (оригинальный код) ---
                    from rental_system import RentalManager
                    expired = rm.get_expired_sessions()
                    for session in expired:
                        logger.info(f"Истекла сессия {session.session_id}: {session.game_name}")
                        ended = rm.end_session(session.session_id)
                        if ended and callback:
                            # Telegram кнопки продления
                            if cardinal.telegram and cardinal.telegram.bot:
                                owner_id = getattr(cardinal.telegram, "owner_chat_id", None)
                                if owner_id:
                                    try:
                                        text = (
                                            f"⏰ <b>Аренда истекла!</b>\n\n"
                                            f"👤 Чат: {session.user_id}\n"
                                            f"🎮 {session.game_name}\n"
                                            f"🆔 <code>{session.session_id}</code>\n\n"
                                            f"Продлить аренду?"
                                        )
                                        extend_kb = _extend_keyboard(int(session.user_id))
                                        cardinal.telegram.bot.send_message(owner_id, text, reply_markup=extend_kb)
                                    except Exception as _te:
                                        logger.error(f"rental expiry TG notify: {_te}")
                            callback(session)

                    _t.sleep(interval)
                except Exception as ex:
                    logger.error(f"_patched_check: {ex}")
                    _t.sleep(interval)

        import types as _types
        rm._check_expired_sessions = _types.MethodType(
            lambda self, interval, cb: _patched_check(interval, cb), rm
        )
        logger.info(f"RentalManager патч: предупреждение за {WARN_BEFORE_SECS // 60} мин")
    except Exception as ex:
        logger.error(f"patch_rental_checker_with_warning: {ex}", exc_info=True)


class _noop_ctx:
    """Заглушка контекстного менеджера если у rm нет _lock."""
    def __enter__(self): return self
    def __exit__(self, *a): pass
