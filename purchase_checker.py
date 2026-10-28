"""
Модуль для проверки покупок перед выдачей Guard кодов.
Покупки хранятся бессрочно (очищаются только вручную или при перезапуске бота).
Лимит повторных запросов !code НЕ применяется — покупатель может запрашивать код сколько угодно раз.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Set
import logging

if TYPE_CHECKING:
    from cardinal import Cardinal

logger = logging.getLogger("PurchaseChecker")


class PurchaseChecker:
    """Отслеживает покупки и разрешает/запрещает доступ к Guard кодам."""

    def __init__(self, cardinal: "Cardinal"):
        self.cardinal = cardinal
        # Множество chat_id пользователей, совершивших покупку
        self.purchased_chat_ids: Set[int] = set()
        # Привязка чата к Steam-аккаунту (для корректного !code при нескольких аккаунтах)
        self.account_for_chat: dict[int, str] = {}

    def set_account_for_chat(self, chat_id: int, steam_login: str):
        """Запоминает, какой Steam-аккаунт выдан в чат (для маршрутизации !code)."""
        self.account_for_chat[chat_id] = steam_login

    def get_account_for_chat(self, chat_id: int) -> str | None:
        """Возвращает Steam-логин, привязанный к чату, или None."""
        return self.account_for_chat.get(chat_id)

    # ------------------------------------------------------------------
    # Регистрация покупки
    # ------------------------------------------------------------------

    def add_purchase(self, chat_id: int, username: str):
        """
        Регистрирует покупку. Вызывается из deliver_product_handler после успешной выдачи.

        :param chat_id: ID чата покупателя
        :param username: ник покупателя
        """
        self.purchased_chat_ids.add(chat_id)
        logger.info(f"PurchaseChecker: покупка зарегистрирована — {username} (chat_id: {chat_id})")

    # ------------------------------------------------------------------
    # Проверка доступа к Guard коду
    # ------------------------------------------------------------------

    def can_get_guard_code(self, chat_id: int, username: str) -> bool:
        """
        Возвращает True если пользователь когда-либо совершал покупку.

        :param chat_id: ID чата покупателя
        :param username: ник покупателя
        """
        allowed = chat_id in self.purchased_chat_ids
        if allowed:
            logger.info(f"PurchaseChecker: Guard код разрешён для {username}")
        else:
            logger.warning(f"PurchaseChecker: Guard код запрещён для {username} — нет покупки")
        return allowed

    def can_request_code(self, chat_id: int, username: str) -> tuple[bool, str]:
        """
        Проверяет, может ли пользователь запросить Guard код.
        Отказ если: нет покупки, или !code отозван (истекла аренда / оффлайн подтверждён).
        """
        if not self.can_get_guard_code(chat_id, username):
            return (
                False,
                "❌ Код Steam Guard доступен только после покупки товара.\n"
                "Если вы уже купили — обратитесь к продавцу.",
            )
        # Проверяем сессию — не истекла ли / не отозван ли !code
        try:
            from rental_system import get_rental_manager
            rm = get_rental_manager()
            if rm:
                session = rm.get_active_session_for_chat(chat_id)
                if session is None:
                    # Нет активной сессии — ищем завершённую с отозванным кодом
                    for s in rm.sessions.values():
                        if str(s.user_id) == str(chat_id) and s.code_revoked:
                            return (
                                False,
                                "⏰ Ваша сессия завершена — доступ к коду Steam Guard закрыт.\n"
                                "Для продолжения оформите новую покупку.",
                            )
                elif session.code_revoked:
                    return (
                        False,
                        "⏰ Ваша сессия завершена — доступ к коду Steam Guard закрыт.\n"
                        "Для продолжения оформите новую покупку.",
                    )
        except Exception:
            pass
        return True, ""

    def record_code_attempt(self, chat_id: int, username: str) -> int:
        """
        Заглушка для совместимости с guard_command_handler.
        Лимита попыток нет — всегда возвращает -1 (безлимитно).
        """
        logger.debug(f"PurchaseChecker: запрос !code от {username}")
        return -1  # -1 = безлимитно

    def get_remaining_attempts(self, chat_id: int) -> int:
        """Возвращает -1 — безлимитные попытки."""
        return -1

    def reset_attempts(self, chat_id: int, username: str):
        """Заглушка — попытки не ограничены, сбрасывать нечего."""
        pass

    # ------------------------------------------------------------------
    # Управление покупками
    # ------------------------------------------------------------------

    def remove_purchase(self, chat_id: int, username: str):
        """
        Удаляет покупку пользователя (например, при возврате).

        :param chat_id: ID чата покупателя
        :param username: ник покупателя
        """
        self.purchased_chat_ids.discard(chat_id)
        logger.info(f"PurchaseChecker: покупка удалена для {username} (chat_id: {chat_id})")

    def clear_all(self):
        """Сбрасывает все покупки (например, при перезапуске бота)."""
        self.purchased_chat_ids.clear()
        logger.info("PurchaseChecker: все покупки сброшены")


# ----------------------------------------------------------------------
# Глобальный экземпляр
# ----------------------------------------------------------------------

_purchase_checker: PurchaseChecker | None = None


def init_purchase_checker(cardinal: "Cardinal") -> PurchaseChecker:
    """Инициализирует и возвращает глобальный PurchaseChecker."""
    global _purchase_checker
    _purchase_checker = PurchaseChecker(cardinal)
    return _purchase_checker


def get_purchase_checker() -> PurchaseChecker | None:
    """Возвращает глобальный PurchaseChecker (или None до инициализации)."""
    return _purchase_checker
