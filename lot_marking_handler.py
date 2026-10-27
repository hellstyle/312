"""
Хендлер для маркировки лотов пометкой автовыдачи через Telegram.
"""

from __future__ import annotations
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from cardinal import Cardinal

from lot_manager import get_lot_manager

logger = logging.getLogger("LotMarking")


def init_lot_marking_handler(cardinal: "Cardinal", *args):
    """
    Регистрирует команду /marklots в Telegram боте.
    Вызывается на BIND_TO_POST_INIT — после инициализации Telegram.
    """
    tg = cardinal.telegram
    if not tg:
        logger.warning("Telegram не инициализирован — команда /marklots не зарегистрирована.")
        return

    def mark_lots_command(message):
        """Обработчик команды /marklots."""
        if message.from_user.id not in tg.authorized_users:
            tg.bot.reply_to(message, "❌ У вас нет прав для выполнения этой команды.")
            return

        tg.bot.reply_to(message, "🔄 Начинаю маркировку лотов с автовыдачей...")

        lot_mgr = get_lot_manager()
        if lot_mgr:
            changed_count = lot_mgr.mark_lots_with_auto_delivery()
            tg.bot.reply_to(
                message,
                f"✅ Маркировка завершена! Добавлено пометок автовыдачи: {changed_count} лотов."
            )
        else:
            tg.bot.reply_to(message, "❌ Менеджер лотов не инициализирован.")

    # Используем правильный метод TGBot для регистрации команды
    tg.msg_handler(mark_lots_command, commands=["marklots"])
    logger.info("Зарегистрирована команда /marklots для маркировки лотов")


# BIND_TO_POST_INIT — вызывается после полной инициализации, включая Telegram
BIND_TO_POST_INIT = [init_lot_marking_handler]
