#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль для автоматического создания лотов на основе библиотеки Steam.
Поддерживает оффлайн активацию и аренду по времени.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, List, Dict, Optional
import logging
import configparser
import time

if TYPE_CHECKING:
    from cardinal import Cardinal

logger = logging.getLogger("AutoLotCreator")


class AutoLotCreator:
    """Автоматическое создание лотов на основе библиотеки Steam."""
    
    def __init__(self, cardinal: "Cardinal"):
        self.cardinal = cardinal
        self.config = cardinal.MAIN_CFG
        self.enabled = False
        self.steam_id = None
        self.api_key = None
        self.category_id = None
        self.lot_type = "offline"  # offline или rental
        self.rental_hours = 24
        self.rental_price = 100
        self.offline_price = 50
        self.mark_auto_delivery = True
        self.auto_raise_lots = True
        self.auto_delivery_marker = "✔️ АВТОВЫДАЧА"
        self.manual_games_list = []  # Ручной список игр
        self.create_templates = False  # Создавать шаблоны вместо лотов
        
    def init_from_config(self):
        """Инициализация из конфигурации."""
        try:
            if not self.config.has_section("SteamLibrary"):
                logger.warning("Секция SteamLibrary не найдена в конфиге")
                return False
            
            self.enabled = self.config.getboolean("SteamLibrary", "enabled", fallback=False)
            if not self.enabled:
                logger.info("AutoLotCreator отключен в конфиге")
                return False
            
            self.steam_id = self.config.get("SteamLibrary", "steam_id", fallback=None)
            self.api_key = self.config.get("SteamLibrary", "api_key", fallback=None)
            self.category_id = self.config.get("SteamLibrary", "category_id", fallback=None)
            self.lot_type = self.config.get("SteamLibrary", "lot_type", fallback="offline")
            self.rental_hours = self.config.getint("SteamLibrary", "rental_hours", fallback=24)
            self.rental_price = self.config.getint("SteamLibrary", "rental_price", fallback=100)
            self.offline_price = self.config.getint("SteamLibrary", "offline_price", fallback=50)
            self.mark_auto_delivery = self.config.getboolean("SteamLibrary", "mark_auto_delivery", fallback=True)
            self.auto_raise_lots = self.config.getboolean("SteamLibrary", "auto_raise_lots", fallback=True)
            self.auto_create_lots = self.config.getboolean("SteamLibrary", "auto_create_lots", fallback=True)
            self.create_templates = self.config.getboolean("SteamLibrary", "create_templates", fallback=False)
            
            # Загружаем ручной список игр (через запятую)
            manual_games_str = self.config.get("SteamLibrary", "manual_games_list", fallback="")
            if manual_games_str:
                self.manual_games_list = [game.strip() for game in manual_games_str.split(",") if game.strip()]
                logger.info(f"Загружен ручной список игр: {len(self.manual_games_list)} игр")
            
            # Не требуем API Key если есть ручной список
            if not self.steam_id:
                logger.warning("Steam ID не указан в конфиге")
            
            if not self.api_key and not self.manual_games_list:
                logger.warning("API Key не указан и ручной список пуст. Создание лотов через API невозможно.")
                return False
            
            logger.info(f"AutoLotCreator инициализирован: lot_type={self.lot_type}, category_id={self.category_id}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка инициализации AutoLotCreator: {e}")
            return False
    
    def get_existing_lots(self) -> set:
        """Получает список существующих названий лотов."""
        try:
            existing_lots = set()
            
            # Получаем профиль FunPay
            profile = self.cardinal.curr_profile
            if not profile:
                logger.warning("Профиль не загружен")
                return set()
            
            # Проверяем структуру профиля
            if hasattr(profile, 'categories'):
                categories = profile.categories
            elif hasattr(profile, 'lots'):
                # Если категории нет, проверяем лоты напрямую
                for lot in profile.lots:
                    existing_lots.add(lot.name.lower())
                logger.info(f"Найдено {len(existing_lots)} существующих лотов (прямо из профиля)")
                return existing_lots
            else:
                logger.warning("Не удалось найти категории или лоты в профиле")
                return set()
            
            # Проходим по категориям и лотам
            for category in categories:
                if hasattr(category, 'lots'):
                    for lot in category.lots:
                        existing_lots.add(lot.name.lower())
            
            logger.info(f"Найдено {len(existing_lots)} существующих лотов")
            return existing_lots
        except Exception as e:
            logger.error(f"Ошибка получения существующих лотов: {e}")
            return set()
    
    def get_steam_library(self) -> List[Dict]:
        """Получает список игр из библиотеки Steam или ручного списка."""
        try:
            # Если есть ручной список игр - используем его
            if self.manual_games_list:
                games = []
                for game_name in self.manual_games_list:
                    games.append({
                        'name': game_name,
                        'appid': 0,  # ID неизвестен без API
                        'playtime_forever': 0
                    })
                logger.info(f"Использован ручной список игр: {len(games)} игр")
                return games
            
            # Иначе пытаемся получить через Steam API
            if self.api_key and self.steam_id:
                from steam_library_scanner import SteamLibraryScanner
                
                scanner = SteamLibraryScanner(self.steam_id, self.api_key)
                games = scanner.get_library_games()
                
                if games:
                    logger.info(f"Получено {len(games)} игр из библиотеки Steam")
                    return games
                else:
                    logger.warning("Не удалось получить игры из библиотеки Steam")
                    return []
            else:
                logger.warning("Нет API Key и ручного списка игр")
                return []
                
        except Exception as e:
            logger.error(f"Ошибка получения библиотеки Steam: {e}")
            return []
    
    def generate_lot_description(self, game: Dict) -> str:
        """Генерирует описание для лота на основе типа."""
        game_name = game.get('name', 'Unknown Game')
        
        if self.lot_type == "rental":
            hours = self.rental_hours
            price = self.rental_price
            
            description = (
                f"🎮 Аренда игры {game_name} на {hours} часов\n\n"
                f"💰 Цена: {price} рублей\n\n"
                f"📋 Что включено:\n"
                f"• Доступ к аккаунту Steam\n"
                f"• Возможность скачать игру\n"
                f"• Аренда на {hours} часов\n\n"
                f"⚠️ Правила:\n"
                f"• Не меняйте пароль\n"
                f"• Не выходите из аккаунта\n"
                f"• Играйте только в указанную игру\n\n"
                f"🔐 Для входа понадобится Guard код (!code)\n\n"
                f"🤖 {self.auto_delivery_marker if self.mark_auto_delivery else ''}"
            )
        else:  # offline
            price = self.offline_price
            
            description = (
                f"🎮 Оффлайн активация {game_name}\n\n"
                f"💰 Цена: {price} рублей\n\n"
                f"📋 Что вы получаете:\n"
                f"• Постоянный доступ к игре в оффлайн режиме\n"
                f"• Аккаунт для активации\n"
                f"• Инструкция по активации\n\n"
                f"⚠️ Важно:\n"
                f"• Не меняйте пароль\n"
                f"• Не выходите из Steam после активации\n"
                f"• Аккаунт для активации, не для постоянного использования\n\n"
                f"🔐 Для активации понадобится Guard код (!code)\n\n"
                f"🤖 {self.auto_delivery_marker if self.mark_auto_delivery else ''}"
            )
        
        return description

    def generate_auto_delivery_template(self, game_name: str, lot_type: str) -> str:
        """Генерирует шаблон для auto_delivery.cfg."""
        
        if lot_type == "rental":
            hours = self.rental_hours
            description = (
                f"🎮 Спасибо за покупку аренды {game_name} на {hours} часов, $username! 🎉\n\n"
                f"📝 ДАННЫЕ ДОСТУПА:\n"
                f"$product\n\n"
                f"⏱ Аренда: {hours} часов\n"
                f"🔐 ДЛЯ ПОЛУЧЕНИЯ КОДА ПОДТВЕРЖДЕНИЯ: Отправьте команду: !code\n\n"
                f"⚠️ ВАЖНО ДЛЯ АРЕНДЫ:\n"
                f"• Код действителен 30-45 секунд\n"
                f"• Не меняйте пароль аккаунта\n"
                f"• Не выходите из аккаунта до окончания аренды\n"
                f"• Играйте только в {game_name}\n"
                f"• Аренда закончится автоматически\n\n"
                f"📋 ИНСТРУКЦИЯ:\n"
                f"1. Войдите в Steam с полученными данными\n"
                f"2. Скачайте {game_name}\n"
                f"3. При запросе кода отправьте !code\n"
                f"4. Наслаждайтесь игрой! 🎮\n\n"
                f"📞 Нужна помощь? Напишите в поддержку!"
            )
        else:  # offline
            description = (
                f"🎮 Спасибо за покупку оффлайн активации {game_name}, $username! 🎉\n\n"
                f"📝 ДАННЫЕ ДОСТУПА:\n"
                f"$product\n\n"
                f"🔐 ДЛЯ ПОЛУЧЕНИЯ КОДА ПОДТВЕРЖДЕНИЯ: Отправьте команду: !code\n\n"
                f"⚠️ ВАЖНО ДЛЯ ОФФЛАЙН АКТИВАЦИИ:\n"
                f"• Код действителен 30-45 секунд\n"
                f"• Доступно 3 попытки запроса кода\n"
                f"• Используйте код сразу после получения\n"
                f"• Запустите Steam в оффлайн режиме перед активацией\n\n"
                f"📋 ИНСТРУКЦИЯ ПО ОФФЛАЙН АКТИВАЦИИ:\n"
                f"1. Войдите в Steam с полученными данными\n"
                f"2. Переведите Steam в оффлайн режим\n"
                f"3. Запустите {game_name} для активации\n"
                f"4. После активации можно играть без интернета\n\n"
                f"📞 Нужна помощь? Напишите в поддержку!"
            )
        
        return description
    
    def create_lot(self, game: Dict) -> bool:
        """Создаёт лот на FunPay."""
        try:
            game_name = game.get('name', 'Unknown Game')
            app_id = game.get('appid', 0)
            
            description = self.generate_lot_description(game)
            
            # Определяем цену
            if self.lot_type == "rental":
                price = self.rental_price
            else:
                price = self.offline_price
            
            # Формируем название лота
            if self.lot_type == "rental":
                lot_name = f"Аренда {game_name} ({self.rental_hours}ч)"
            else:
                lot_name = f"{game_name} Оффлайн активация"
            
            # Добавляем маркер автовыдачи
            if self.mark_auto_delivery:
                lot_name = f"{lot_name} {self.auto_delivery_marker}"
            
            # Создаём лот через FunPay API
            # (Здесь нужно использовать API FunPay для создания лотов)
            # К сожалению, FunPay API не предоставляет публичный метод создания лотов
            # Поэтому это будет заглушка для будущего
            
            logger.info(f"Создание лота: {lot_name} (цена: {price} RUB)")
            logger.debug(f"Описание: {description}")
            
            # TODO: Добавить реальное создание лота через FunPay API
            # Когда API станет доступным
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка создания лота для {game.get('name')}: {e}")
            return False
    
    def scan_and_create_lots(self):
        """Сканирует библиотеку и создаёт недостающие шаблоны или лоты."""
        if not self.enabled or not self.auto_create_lots:
            logger.info("AutoLotCreator отключен или авто-создание лотов выключено")
            return
        
        try:
            logger.info("Начинаю сканирование библиотеки Steam...")
            
            # Получаем существующие лоты/шаблоны
            existing_lots = self.get_existing_lots()
            
            # Получаем библиотеку Steam
            games = self.get_steam_library()
            if not games:
                logger.warning("Нет игр в библиотеке Steam")
                return
            
            # Фильтруем игры без названия
            games = [g for g in games if g.get('name')]
            
            # Находим игры, которых нет в лотах
            new_games = []
            for game in games:
                game_name = game.get('name', '').lower()
                if game_name not in existing_lots:
                    new_games.append(game)
            
            logger.info(f"Найдено {len(new_games)} новых игр")
            
            if self.create_templates:
                # Создаём шаблоны в auto_delivery.cfg
                self.create_templates_for_games(new_games)
            else:
                # Создаём лоты (заглушка - не работает без API)
                created_count = 0
                for game in new_games:
                    if self.create_lot(game):
                        created_count += 1
                        time.sleep(1)
                logger.info(f"Создано {created_count} лотов (заглушка - нужен API FunPay)")
            
        except Exception as e:
            logger.error(f"Ошибка при сканировании и создании: {e}")

    def create_templates_for_games(self, games: List[Dict]):
        """Создаёт шаблоны в auto_delivery.cfg для указанных игр."""
        try:
            import configparser
            config_path = "configs/auto_delivery.cfg"
            
            # Читаем существующий конфиг
            config = configparser.ConfigParser()
            config.read(config_path, encoding='utf-8')
            
            created_count = 0
            for game in games:
                game_name = game.get('name', 'Unknown Game')
                section_name = game_name  # Используем название игры как секцию
                
                # Пропускаем если секция уже существует
                if config.has_section(section_name):
                    logger.info(f"Шаблон для '{game_name}' уже существует")
                    continue
                
                # Создаём секцию
                config.add_section(section_name)
                
                # Генерируем описание
                description = self.generate_auto_delivery_template(game_name, self.lot_type)
                config.set(section_name, "response", description)
                config.set(section_name, "telegramNotification", "1")
                config.set(section_name, "enabled", "1")
                
                created_count += 1
                logger.info(f"Создан шаблон для '{game_name}'")
            
            # Сохраняем конфиг
            with open(config_path, 'w', encoding='utf-8') as f:
                config.write(f)
            
            logger.info(f"Создано {created_count} шаблонов в auto_delivery.cfg")
            
        except Exception as e:
            logger.error(f"Ошибка создания шаблонов: {e}")
    
    def enable_auto_raise(self):
        """Включает автоподнятие для созданных лотов."""
        if not self.auto_raise_lots:
            logger.info("Автоподнятие лотов отключено")
            return
        
        try:
            # TODO: Реализовать автоподнятие через FunPay API
            logger.info("Автоподнятие лотов включено")
        except Exception as e:
            logger.error(f"Ошибка включения автоподнятия: {e}")


def init_auto_lot_creator(cardinal: "Cardinal") -> Optional[AutoLotCreator]:
    """Инициализирует и возвращает AutoLotCreator."""
    try:
        creator = AutoLotCreator(cardinal)
        if creator.init_from_config():
            logger.info("AutoLotCreator успешно инициализирован")
            return creator
        else:
            logger.warning("AutoLotCreator не инициализирован")
            return None
    except Exception as e:
        logger.error(f"Ошибка инициализации AutoLotCreator: {e}")
        return None


def get_auto_lot_creator() -> Optional[AutoLotCreator]:
    """Возвращает существующий экземпляр AutoLotCreator."""
    from cardinal import Cardinal
    if hasattr(Cardinal, "instance"):
        cardinal = Cardinal.instance
        if hasattr(cardinal, "auto_lot_creator"):
            return cardinal.auto_lot_creator
    return None