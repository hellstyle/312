#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Система аренды игр по времени с автоматическим управлением сессиями.
"""

import time
import json
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from threading import Thread, Lock
import configparser

logger = logging.getLogger("RentalSystem")


class RentalSession:
    """Класс для хранения информации о сессии аренды."""
    
    def __init__(self, session_id: str, user_id: str, game_name: str, 
                 account_name: str, duration_hours: int, start_time: float = None,
                 steam_login: str = None, steamid: str = None,
                 profile_baseline: dict = None):
        self.session_id = session_id
        self.user_id = user_id  # FunPay user ID
        self.game_name = game_name
        self.account_name = account_name
        # Реальный Steam-аккаунт аренды (привязка к shared_secret / файлу товара).
        # account_name раньше = юзернейм ФП-бота — это был баг; steam_login чинит его.
        self.steam_login = steam_login or account_name
        self.steamid = steamid  # steamid64 (опц., для мониторинга профиля)
        self.profile_baseline = profile_baseline  # эталон профиля (опц.)
        self.duration_hours = duration_hours
        self.start_time = start_time if start_time else time.time()
        self.end_time = self.start_time + (duration_hours * 3600)
        self.active = True
        self.game_downloaded = False  # Игрок скачал игру
        self.game_selected = False  # Игрок выбрал игру
        self.notification_sent = False  # Уведомление об окончании отправлено
    
    def is_expired(self) -> bool:
        """Проверяет, истекла ли сессия."""
        return time.time() > self.end_time
    
    def get_remaining_time(self) -> int:
        """Возвращает оставшееся время в секундах."""
        remaining = self.end_time - time.time()
        return max(0, int(remaining))
    
    def get_remaining_hours(self) -> float:
        """Возвращает оставшееся время в часах."""
        return self.get_remaining_time() / 3600
    
    def mark_game_downloaded(self):
        """Отмечает, что игра скачана - начинается отсчет времени."""
        if not self.game_downloaded:
            self.game_downloaded = True
            self.start_time = time.time()  # Перезапускаем отсчет времени
            self.end_time = self.start_time + (self.duration_hours * 3600)
            logger.info(f"Сессия {self.session_id}: игра скачана, время обновлено")
    
    def mark_game_selected(self):
        """Отмечает, что игра выбрана."""
        self.game_selected = True
        logger.info(f"Сессия {self.session_id}: игра {self.game_name} выбрана")
    
    def to_dict(self) -> dict:
        """Сонвертирует сессию в словарь для сохранения."""
        return {
            'session_id': self.session_id,
            'user_id': self.user_id,
            'game_name': self.game_name,
            'account_name': self.account_name,
            'steam_login': self.steam_login,
            'steamid': self.steamid,
            'profile_baseline': self.profile_baseline,
            'duration_hours': self.duration_hours,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'active': self.active,
            'game_downloaded': self.game_downloaded,
            'game_selected': self.game_selected,
            'notification_sent': self.notification_sent
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'RentalSession':
        """Создает сессию из словаря."""
        session = cls(
            data['session_id'],
            data['user_id'],
            data['game_name'],
            data['account_name'],
            data['duration_hours'],
            data['start_time'],
            steam_login=data.get('steam_login'),
            steamid=data.get('steamid'),
            profile_baseline=data.get('profile_baseline'),
        )
        session.end_time = data['end_time']
        session.active = data['active']
        session.game_downloaded = data['game_downloaded']
        session.game_selected = data['game_selected']
        session.notification_sent = data['notification_sent']
        return session


class RentalManager:
    """Менеджер системы аренды."""
    
    def __init__(self, storage_path: str = 'storage/rental_sessions.json'):
        self.storage_path = storage_path
        # Отдельный файл для паролей Steam-аккаунтов (чтобы rental_sessions.json
        # оставался безопасным для отладки/передачи). Ключ — session_id.
        self.credentials_path = os.path.join(os.path.dirname(storage_path), 'rental_credentials.json')
        self.sessions: Dict[str, RentalSession] = {}  # {session_id: RentalSession}
        self.user_sessions: Dict[str, List[str]] = {}  # {user_id: [session_ids]}
        self.account_sessions: Dict[str, List[str]] = {}  # {account_name: [session_ids]}
        self.credentials: Dict[str, dict] = {}  # {session_id: {"steam_login":..,"steam_password":..}}
        self.lock = Lock()
        self.running = False
        self.checker_thread = None
        
        # Создаем директорию для хранения
        os.makedirs(os.path.dirname(storage_path), exist_ok=True)
        
        # Загружаем сохраненные сессии и учётки
        self.load_sessions()
        self.load_credentials()
    
    def create_session(self, user_id: str, game_name: str, account_name: str, 
                      duration_hours: int, steam_login: str = None,
                      steam_password: str = None, steamid: str = None,
                      profile_baseline: dict = None) -> RentalSession:
        """Создает новую сессию аренды."""
        with self.lock:
            session_id = f"{user_id}_{game_name}_{int(time.time())}"
            session = RentalSession(session_id, user_id, game_name, account_name,
                                      duration_hours, steam_login=steam_login,
                                      steamid=steamid, profile_baseline=profile_baseline)
            
            self.sessions[session_id] = session
            
            # Добавляем в индексы
            if user_id not in self.user_sessions:
                self.user_sessions[user_id] = []
            self.user_sessions[user_id].append(session_id)
            
            if account_name not in self.account_sessions:
                self.account_sessions[account_name] = []
            self.account_sessions[account_name].append(session_id)

            # Сохраняем пароль Steam-аккаунта отдельно (для кика по окончании)
            if steam_login and steam_password:
                self.store_credential(session_id, steam_login, steam_password)
            
            self.save_sessions()
            logger.info(f"Создана сессия {session_id}: {game_name} на {duration_hours} часов"
                        f"{f' (Steam: {steam_login})' if steam_login else ''}")
            
            return session
    
    def get_session(self, session_id: str) -> Optional[RentalSession]:
        """Получает сессию по ID."""
        with self.lock:
            return self.sessions.get(session_id)
    
    def get_user_sessions(self, user_id: str) -> List[RentalSession]:
        """Получает все сессии пользователя."""
        with self.lock:
            session_ids = self.user_sessions.get(user_id, [])
            return [self.sessions[sid] for sid in session_ids if sid in self.sessions]
    
    def get_account_sessions(self, account_name: str) -> List[RentalSession]:
        """Получает все сессии аккаунта."""
        with self.lock:
            session_ids = self.account_sessions.get(account_name, [])
            return [self.sessions[sid] for sid in session_ids if sid in self.sessions]
    
    def mark_game_downloaded(self, session_id: str) -> bool:
        """Отмечает, что игра скачана и начинается отсчет времени."""
        with self.lock:
            session = self.sessions.get(session_id)
            if session:
                session.mark_game_downloaded()
                self.save_sessions()
                return True
            return False
    
    def mark_game_selected(self, session_id: str) -> bool:
        """Отмечает, что игра выбрана."""
        with self.lock:
            session = self.sessions.get(session_id)
            if session:
                session.mark_game_selected()
                self.save_sessions()
                return True
            return False
    
    def end_session(self, session_id: str) -> bool:
        """Завершает сессию."""
        with self.lock:
            session = self.sessions.get(session_id)
            if session:
                session.active = False
                self.save_sessions()
                logger.info(f"Сессия {session_id} завершена")
                return True
            return False
    
    def delete_session(self, session_id: str) -> bool:
        """Удаляет сессию."""
        with self.lock:
            session = self.sessions.get(session_id)
            if session:
                # Удаляем из индексов
                if session.user_id in self.user_sessions:
                    self.user_sessions[session.user_id] = [
                        sid for sid in self.user_sessions[session.user_id] if sid != session_id
                    ]
                
                if session.account_name in self.account_sessions:
                    self.account_sessions[session.account_name] = [
                        sid for sid in self.account_sessions[session.account_name] if sid != session_id
                    ]
                
                del self.sessions[session_id]
                self.delete_credential(session_id)
                self.save_sessions()
                logger.info(f"Сессия {session_id} удалена")
                return True
            return False
    
    def get_expired_sessions(self) -> List[RentalSession]:
        """Получает все истекшие сессии."""
        with self.lock:
            return [session for session in self.sessions.values() 
                   if session.active and session.is_expired()]
    
    def save_sessions(self):
        """Сохраняет сессии в файл."""
        try:
            data = {
                'sessions': [session.to_dict() for session in self.sessions.values()],
                'user_sessions': self.user_sessions,
                'account_sessions': self.account_sessions
            }
            
            with open(self.storage_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            logger.error(f"Ошибка сохранения сессий: {e}")
    
    def load_sessions(self):
        """Загружает сессии из файла."""
        try:
            if not os.path.exists(self.storage_path):
                return
            
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Загружаем сессии
            for session_data in data.get('sessions', []):
                session = RentalSession.from_dict(session_data)
                self.sessions[session.session_id] = session
            
            # Загружаем индексы
            self.user_sessions = data.get('user_sessions', {})
            self.account_sessions = data.get('account_sessions', {})
            
            logger.info(f"Загружено {len(self.sessions)} сессий")
            
        except Exception as e:
            logger.error(f"Ошибка загрузки сессий: {e}")
    
    def store_credential(self, session_id: str, steam_login: str, steam_password: str):
        """Сохраняет пароль Steam-аккаунта для сессии (отдельный файл)."""
        self.credentials[session_id] = {"steam_login": steam_login, "steam_password": steam_password}
        self.save_credentials()

    def get_credential(self, session_id: str) -> str | None:
        """Возвращает пароль Steam-аккаунта для сессии или None."""
        cred = self.credentials.get(session_id)
        return cred.get("steam_password") if cred else None

    def delete_credential(self, session_id: str):
        if session_id in self.credentials:
            del self.credentials[session_id]
            self.save_credentials()

    def save_credentials(self):
        try:
            with open(self.credentials_path, 'w', encoding='utf-8') as f:
                json.dump(self.credentials, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения учёток аренды: {e}")

    def load_credentials(self):
        try:
            if not os.path.exists(self.credentials_path):
                return
            with open(self.credentials_path, 'r', encoding='utf-8') as f:
                self.credentials = json.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки учёток аренды: {e}")

    def start_checker(self, interval: int = 60, callback=None):
        """Запускает проверку истекших сессий."""
        if self.running:
            return
        
        self.running = True
        self.checker_thread = Thread(target=self._check_expired_sessions, 
                                    args=(interval, callback), daemon=True)
        self.checker_thread.start()
        logger.info(f"Запущен проверщик истекших сессий (интервал: {interval} сек)")
    
    def stop_checker(self):
        """Останавливает проверку истекших сессий."""
        self.running = False
        if self.checker_thread:
            self.checker_thread.join(timeout=5)
        logger.info("Проверщик истекших сессий остановлен")
    
    def _check_expired_sessions(self, interval: int, callback):
        """Фоновая проверка истекших сессий."""
        while self.running:
            try:
                expired_sessions = self.get_expired_sessions()
                
                for session in expired_sessions:
                    logger.info(f"Истекла сессия {session.session_id}: {session.game_name}")
                    
                    # Завершаем сессию
                    self.end_session(session.session_id)
                    
                    # Вызываем callback если есть
                    if callback:
                        callback(session)
                
                time.sleep(interval)
                
            except Exception as e:
                logger.error(f"Ошибка в проверке истекших сессий: {e}")
                time.sleep(interval)


# Глобальный экземпляр
_rental_manager: RentalManager = None


def init_rental_manager(storage_path: str = 'storage/rental_sessions.json') -> RentalManager:
    """Инициализация глобального менеджера аренды."""
    global _rental_manager
    _rental_manager = RentalManager(storage_path)
    return _rental_manager


def get_rental_manager() -> RentalManager:
    """Получение глобального менеджера аренды."""
    return _rental_manager


if __name__ == "__main__":
    print("Модуль Rental System загружен.")
    print("Функции:")
    print("- Управление сессиями аренды по времени")
    print("- Автоматическое отключение по истечении времени")
    print("- Отслеживание скачивания игры")
    print("- Сохранение состояния в файл")
