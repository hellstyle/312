"""
Модуль-интеграция Guard систем с FunPay Cardinal автоответчиком.
Поддерживает как Gmail, так и shared_secret методы.
"""

from gmail_guard_integration import GmailGuardIntegration
from shared_secret_guard import SharedSecretManager
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from cardinal import Cardinal

logger = logging.getLogger("GuardHandler")


class GuardHandler:
    """Обработчик Guard кодов для интеграции с FunPay Cardinal."""
    
    def __init__(self, cardinal: 'Cardinal'):
        self.cardinal = cardinal
        self.gmail_integration = None
        self.shared_secret_manager = None
        self.enabled = False
        self.use_shared_secret = False  # Приоритет shared_secret над Gmail
        
    def init_gmail(self, email_address: str, app_password: str) -> bool:
        """
        Инициализация Gmail интеграции.
        
        :param email_address: Gmail адрес
        :param app_password: Пароль приложения Gmail
        :return: True если успешно
        """
        try:
            self.gmail_integration = GmailGuardIntegration(email_address, app_password)
            if self.gmail_integration.connect():
                # Проверка каждые 15 секунд для быстрого получения кодов (оффлайн активация)
                self.gmail_integration.start_auto_check(interval=15)
                self.enabled = True
                logger.info("Gmail Guard интеграция успешно инициализирована (проверка каждые 15 сек)")
                return True
            else:
                logger.error("Не удалось подключиться к Gmail")
                return False
        except Exception as e:
            logger.error(f"Ошибка инициализации Gmail: {e}")
            return False
    
    def get_guard_code(self, username: str) -> str | None:
        """
        Получение Guard кода.

        :param username: имя пользователя (не используется — код общий)
        :return: Guard код (5 символов) или None если кода нет
        """
        # Приоритет shared_secret если доступен
        if self.use_shared_secret and self.shared_secret_manager:
            code_info = self.shared_secret_manager.get_code_with_info(username)
            if code_info and code_info['valid']:
                remaining = code_info['remaining_seconds']
                logger.info(f"Shared Secret код для {username}: {code_info['code']} (осталось {remaining} сек)")
                return code_info['code']
            else:
                logger.warning(f"Не удалось получить код через shared_secret для {username}")
        
        # Fallback на Gmail если shared_secret не сработал
        if self.gmail_integration:
            code = self.gmail_integration.get_guard_code(username)
            if code:
                return code
        
        return None

    def get_guard_code_for_account(self, account_name: str) -> str | None:
        """
        Guard код для конкретного Steam-аккаунта (без тихого фолбэка на первый).

        :param account_name: Steam-логин (секция в shared_secrets.cfg)
        :return: Guard код или None
        """
        if self.use_shared_secret and self.shared_secret_manager:
            code = self.shared_secret_manager.get_code(account_name)
            if code:
                logger.info(f"Shared Secret код для аккаунта {account_name}")
                return code
            logger.warning(f"Аккаунт {account_name} не найден в shared_secret — код не сгенерирован")
        if self.gmail_integration:
            return self.gmail_integration.get_guard_code(account_name)
        return None
    
    def init_shared_secret(self) -> bool:
        """
        Инициализация shared_secret менеджера.
        
        :return: True если успешно
        """
        try:
            self.shared_secret_manager = SharedSecretManager()
            self.enabled = True
            self.use_shared_secret = True
            logger.info("Shared Secret Guard успешно инициализирован")
            return True
        except Exception as e:
            logger.error(f"Ошибка инициализации shared_secret: {e}")
            return False
    
    def add_shared_secret_account(self, username: str, shared_secret: str) -> bool:
        """
        Добавляет аккаунт с shared_secret.
        
        :param username: имя пользователя
        :param shared_secret: shared_secret (base64 encoded)
        :return: True если успешно
        """
        if not self.shared_secret_manager:
            logger.error("Shared Secret менеджер не инициализирован")
            return False
        
        success = self.shared_secret_manager.add_account(username, shared_secret)
        if success:
            self.use_shared_secret = True
            self.enabled = True
        return success
    
    def shutdown(self):
        """Завершение работы."""
        if self.gmail_integration:
            self.gmail_integration.stop_auto_check()
            self.gmail_integration.disconnect()
            logger.info("Gmail Guard интеграция остановлена")
        
        if self.shared_secret_manager:
            self.shared_secret_manager = None
            logger.info("Shared Secret Guard остановлен")
        
        self.enabled = False


# Глобальный экземпляр для использования в хендлерах
_guard_handler: GuardHandler = None


def init_guard_handler(cardinal: 'Cardinal') -> GuardHandler:
    """Инициализация глобального обработчика Guard."""
    global _guard_handler
    _guard_handler = GuardHandler(cardinal)
    return _guard_handler


def get_guard_handler() -> GuardHandler:
    """Получение глобального обработчика Guard."""
    return _guard_handler