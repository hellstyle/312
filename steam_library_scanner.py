#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Система сканирования библиотеки Steam и автоматического создания лотов.
"""

import requests
import json
import logging
from typing import List, Dict, Optional
import configparser
import os

logger = logging.getLogger("SteamLibraryScanner")


class SteamLibraryScanner:
    """Сканер библиотеки Steam для автоматического создания лотов."""
    
    def __init__(self, steam_id: str, api_key: str = None):
        self.steam_id = steam_id
        self.api_key = api_key
        self.games = []
        self.base_url = "https://api.steampowered.com"
    
    def get_library_games(self) -> List[Dict]:
        """Получает список игр из библиотеки Steam."""
        try:
            if not self.api_key:
                logger.error("Steam API Key не указан")
                return []
            
            url = f"{self.base_url}/IPlayerService/GetOwnedGames/v0001/"
            params = {
                'key': self.api_key,
                'steamid': self.steam_id,
                'include_appinfo': True,
                'include_played_free_games': True
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if 'response' in data and 'games' in data['response']:
                self.games = data['response']['games']
                logger.info(f"Получено {len(self.games)} игр из библиотеки")
                return self.games
            else:
                logger.error("Неверный формат ответа Steam API")
                return []
                
        except Exception as e:
            logger.error(f"Ошибка получения библиотеки: {e}")
            return []
    
    def get_game_details(self, app_id: int) -> Optional[Dict]:
        """Получает детальную информацию об игре."""
        try:
            url = f"{self.base_url}/ISteamUserStats/GetSchemaForGame/v2/"
            params = {
                'key': self.api_key,
                'appid': app_id
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if 'game' in data and 'availableGameStats' in data['game']:
                return data['game']['availableGameStats']
            return None
            
        except Exception as e:
            logger.error(f"Ошибка получения деталей игры {app_id}: {e}")
            return None
    
    def filter_rental_games(self, min_rating: float = 7.0) -> List[Dict]:
        """Фильтрует игры, подходящие для аренды."""
        rental_games = []
        
        for game in self.games:
            # Пропускаем бесплатные игры без лицензии
            if game.get('playtime_forever', 0) == 0 and game.get('has_community_visible_stats', False):
                continue
            
            # Добавляем подходящие игры
            rental_games.append({
                'appid': game['appid'],
                'name': game['name'],
                'playtime_hours': game.get('playtime_forever', 0) / 60,
                'img_icon_url': game.get('img_icon_url', ''),
                'img_logo_url': game.get('img_logo_url', ''),
                'has_community_visible_stats': game.get('has_community_visible_stats', False)
            })
        
        logger.info(f"Отфильтровано {len(rental_games)} игр для аренды")
        return rental_games
    
    def generate_lot_description(self, game: Dict, rental_hours: int = 24, price: float = 100) -> str:
        """Генерирует описание лота для игры."""
        description = (
            f"🎮 Аренда игры {game['name']}\n\n"
            f"⏱ Время аренды: {rental_hours} часов\n"
            f"💰 Цена: {price} рублей\n\n"
            f"📋 Условия аренды:\n"
            f"• Оффлайн активация игры\n"
            f"• Время начинается после скачивания игры\n"
            f"• Автоматическое отключение по истечении времени\n"
            f"• Поддержка через бота\n\n"
            f"🔧 Как арендовать:\n"
            f"1. Оформите заказ\n"
            f"2. Получите данные аккаунта\n"
            f"3. Выберите игру командой: !select_game {game['name']}\n"
            f"4. Скачайте игру и отправьте: !downloaded\n"
            f"5. Наслаждайтесь игрой!\n\n"
            f"⚠️ Важно: не выходите в онлайн во время аренды!"
        )
        return description
    
    def generate_lots_batch(self, rental_hours: int = 24, price: float = 100) -> List[Dict]:
        """Генерирует пакеты лотов для всех игр."""
        rental_games = self.filter_rental_games()
        lots = []
        
        for game in rental_games:
            lot = {
                'title': f"Аренда {game['name']} ({rental_hours} ч)",
                'description': self.generate_lot_description(game, rental_hours, price),
                'price': price,
                'category': 'Игры / Аренда',
                'game_appid': game['appid'],
                'game_name': game['name'],
                'rental_hours': rental_hours
            }
            lots.append(lot)
        
        logger.info(f"Сгенерировано {len(lots)} лотов")
        return lots
    
    def save_lots_to_file(self, lots: List[Dict], filename: str = 'storage/generated_lots.json'):
        """Сохраняет сгенерированные лоты в файл."""
        try:
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(lots, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Лоты сохранены в {filename}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка сохранения лотов: {e}")
            return False


class LotCreator:
    """Создатель лотов для FunPay."""
    
    def __init__(self, steam_id: str, api_key: str = None):
        self.scanner = SteamLibraryScanner(steam_id, api_key)
    
    def create_rental_lots(self, rental_hours: int = 24, price: float = 100) -> List[Dict]:
        """Создает лоты для аренды игр."""
        # Получаем библиотеку
        games = self.scanner.get_library_games()
        if not games:
            logger.error("Не удалось получить библиотеку игр")
            return []
        
        # Генерируем лоты
        lots = self.scanner.generate_lots_batch(rental_hours, price)
        
        # Сохраняем в файл
        self.scanner.save_lots_to_file(lots)
        
        return lots
    
    def suggest_games_for_rental(self, limit: int = 10) -> List[Dict]:
        """Предлагает лучшие игры для аренды."""
        rental_games = self.scanner.filter_rental_games()
        
        # Сортируем по популярности (время игры)
        rental_games.sort(key=lambda x: x['playtime_hours'], reverse=True)
        
        return rental_games[:limit]


def load_steam_config() -> Optional[Dict]:
    """Загружает конфигурацию Steam."""
    try:
        config = configparser.ConfigParser()
        config.read('configs/_main.cfg')
        
        steam_config = {
            'steam_id': config.get('Steam', 'steam_id', fallback=None),
            'api_key': config.get('Steam', 'api_key', fallback=None)
        }
        
        return steam_config
        
    except Exception as e:
        logger.error(f"Ошибка загрузки конфигурации Steam: {e}")
        return None


if __name__ == "__main__":
    print("Steam Library Scanner Module")
    print("Функции:")
    print("- Сканирование библиотеки Steam")
    print("- Автоматическое создание лотов")
    print("- Предложение игр для аренды")
    print("- Генерация описаний лотов")
    
    # Тестовый пример
    config = load_steam_config()
    if config and config['steam_id']:
        print(f"\nSteam ID: {config['steam_id']}")
        print("Для использования укажите Steam API Key в configs/_main.cfg")
    else:
        print("\nДля работы добавьте в configs/_main.cfg:")
        print("[Steam]")
        print("steam_id = ВАШ_STEAM_ID")
        print("api_key = ВАШ_STEAM_API_KEY")
