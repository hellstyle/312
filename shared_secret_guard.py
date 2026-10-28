#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль для генерации Steam Guard кодов через shared_secret.
Этот метод не требует Gmail и работает локально.
"""

import hmac
import hashlib
import struct
import time
import base64
import logging
from typing import Optional

logger = logging.getLogger("SharedSecretGuard")


class SharedSecretGuard:
    """Класс для генерации Steam Guard кодов через shared_secret."""
    
    def __init__(self, shared_secret: str):
        """
        Инициализация с shared_secret.
        
        :param shared_secret: shared_secret из maFiles (base64 encoded)
        """
        self.shared_secret = shared_secret
        self.enabled = False
        
        try:
            # Декодируем shared_secret
            self.secret = base64.b64decode(shared_secret)
            self.enabled = True
            logger.info("Shared Guard успешно инициализирован")
        except Exception as e:
            logger.error(f"Ошибка инициализации shared_secret: {e}")
            self.secret = None
    
    # Алфавит Steam Guard — 26 символов (используется для генерации 5-символьного кода)
    STEAM_CHARS = "23456789BCDFGHJKMNPQRTVWXY"

    def generate_code(self, timestamp: Optional[int] = None) -> str:
        """
        Генерирует Steam Guard код для указанного времени.
        
        :param timestamp: Unix timestamp (если None, используется текущее время)
        :return: 5-символьный код (A-Z0-9 Steam alphabet) или None при ошибке
        """
        if not self.enabled or not self.secret:
            return None
        
        try:
            if timestamp is None:
                timestamp = int(time.time())
            
            # Используем временной интервал 30 секунд
            time_interval = timestamp // 30
            
            # Создаем HMAC-SHA1 (8 байт big-endian, как в Steam)
            hmac_obj = hmac.new(self.secret, struct.pack('>Q', time_interval), hashlib.sha1)
            hmac_digest = hmac_obj.digest()
            
            # Извлекаем 4 байта по смещению (dynamic truncation)
            offset = hmac_digest[-1] & 0x0F
            code_int = struct.unpack('>I', hmac_digest[offset:offset + 4])[0]
            code_int &= 0x7FFFFFFF
            
            # Кодируем 5 символов из Steam-алфавита
            code = ""
            for _ in range(5):
                code += self.STEAM_CHARS[code_int % len(self.STEAM_CHARS)]
                code_int //= len(self.STEAM_CHARS)
            
            return code
            
        except Exception as e:
            logger.error(f"Ошибка генерации кода: {e}")
            return None
    
    def get_code_with_time_remaining(self) -> tuple[str, int]:
        """
        Генерирует код и возвращает оставшееся время действия.
        
        :return: (code, remaining_seconds)
        """
        current_time = int(time.time())
        code = self.generate_code(current_time)
        
        # Вычисляем оставшееся время в текущем 30-секундном интервале
        remaining = 30 - (current_time % 30)
        
        return code, remaining
    
    def validate_code(self, code: str, tolerance: int = 1) -> bool:
        """
        Проверяет валидность кода (для тестирования).
        
        :param code: код для проверки
        :param tolerance: количество интервалов для проверки (по умолчанию 1)
        :return: True если код валиден
        """
        current_time = int(time.time())
        
        for i in range(-tolerance, tolerance + 1):
            test_time = current_time + (i * 30)
            test_code = self.generate_code(test_time)
            if test_code == code:
                return True
        
        return False


class SharedSecretManager:
    """Менеджер для работы с несколькими shared_secret."""
    
    def __init__(self):
        self.accounts: dict[str, SharedSecretGuard] = {}  # {username: SharedSecretGuard}
        self.enabled = False
    
    def add_account(self, username: str, shared_secret: str) -> bool:
        """
        Добавляет аккаунт с shared_secret.
        
        :param username: имя пользователя/аккаунта
        :param shared_secret: shared_secret (base64 encoded)
        :return: True если успешно
        """
        try:
            guard = SharedSecretGuard(shared_secret)
            if guard.enabled:
                self.accounts[username] = guard
                logger.info(f"Аккаунт {username} добавлен с shared_secret")
                self.enabled = True
                return True
            return False
        except Exception as e:
            logger.error(f"Ошибка добавления аккаунта {username}: {e}")
            return False
    
    def get_code(self, username: str) -> Optional[str]:
        """
        Получает Guard код для указанного пользователя.
        
        :param username: имя пользователя
        :return: Guard код или None
        """
        if username not in self.accounts:
            logger.warning(f"Аккаунт {username} не найден")
            return None
        
        guard = self.accounts[username]
        code, remaining = guard.get_code_with_time_remaining()
        
        if code:
            logger.info(f"Сгенерирован код для {username} (осталось {remaining} сек)")
        else:
            logger.error(f"Не удалось сгенерировать код для {username}")
        
        return code
    
    def get_code_with_info(self, username: str) -> Optional[dict]:
        """
        Получает Guard код с дополнительной информацией.
        
        :param username: имя пользователя (используется только для логов)
        :return: dict с кодом и информацией или None
        """
        # Если пользователь найден в accounts - используем его
        if username in self.accounts:
            guard = self.accounts[username]
        else:
            # Если пользователь не найден - используем первый доступный аккаунт
            # (Steam Guard код общий для всех)
            if not self.accounts:
                logger.warning(f"Нет доступных аккаунтов с shared_secret")
                return None
            
            first_username = list(self.accounts.keys())[0]
            guard = self.accounts[first_username]
            logger.info(f"Пользователь {username} не найден, используем аккаунт {first_username}")
        
        code, remaining = guard.get_code_with_time_remaining()
        
        if code:
            return {
                'code': code,
                'remaining_seconds': remaining,
                'valid': True
            }
        return None
    
    def remove_account(self, username: str) -> bool:
        """
        Удаляет аккаунт из системы.
        
        :param username: имя пользователя
        :return: True если успешно
        """
        if username in self.accounts:
            del self.accounts[username]
            logger.info(f"Аккаунт {username} удален")
            return True
        return False
    
    def list_accounts(self) -> list[str]:
        """
        Возвращает список зарегистрированных аккаунтов.
        
        :return: список имен пользователей
        """
        return list(self.accounts.keys())


# Глобальный экземпляр
_shared_secret_manager: SharedSecretManager = None


def init_shared_secret_manager() -> SharedSecretManager:
    """Инициализация глобального менеджера shared_secret."""
    global _shared_secret_manager
    _shared_secret_manager = SharedSecretManager()
    return _shared_secret_manager


def get_shared_secret_manager() -> SharedSecretManager:
    """Получение глобального менеджера shared_secret."""
    return _shared_secret_manager


def generate_code_from_secret(shared_secret: str) -> Optional[str]:
    """
    Быстрая генерация кода из shared_secret (без сохранения в менеджере).
    
    :param shared_secret: shared_secret (base64 encoded)
    :return: Guard код или None
    """
    guard = SharedSecretGuard(shared_secret)
    return guard.generate_code()


# Тестовая функция
if __name__ == "__main__":
    print("Модуль Shared Secret Guard загружен.")
    print("Для использования необходимо:")
    print("1. Получить shared_secret из maFiles")
    print("2. Инициализировать менеджер или создать экземпляр SharedSecretGuard")
    print("3. Использовать метод generate_code() для получения кодов")
    print()
    print("Пример:")
    print("manager = SharedSecretManager()")
    print("manager.add_account('username', 'shared_secret_here')")
    print("code = manager.get_code('username')")
