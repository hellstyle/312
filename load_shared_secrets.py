#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Простой скрипт для загрузки shared_secret из конфигурационного файла.
"""

import sys
import configparser
import logging
import os

# Устанавливаем кодировку UTF-8 для Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SharedSecretLoader")

def load_shared_secrets(config_path='configs/shared_secrets.cfg'):
    """
    Загружает shared_secret из конфигурационного файла.
    
    :param config_path: путь к файлу конфигурации
    """
    if not os.path.exists(config_path):
        logger.error(f"Файл конфигурации не найден: {config_path}")
        return False
    
    try:
        from shared_secret_guard import get_shared_secret_manager, init_shared_secret_manager
        
        # Инициализируем менеджер если еще не инициализирован
        init_shared_secret_manager()
        
        config = configparser.ConfigParser()
        config.read(config_path, encoding="utf-8")
        
        manager = get_shared_secret_manager()
        
        loaded_count = 0
        for section in config.sections():
            username = config[section].get('username')
            shared_secret = config[section].get('shared_secret')
            platform = config[section].get('platform', 'steam')
            description = config[section].get('description', '')
            
            if username and shared_secret:
                success = manager.add_account(username, shared_secret)
                if success:
                    loaded_count += 1
                    logger.info(f"OK: Загружен аккаунт: {username} ({platform}) - {description}")
                else:
                    logger.error(f"ERROR: Ошибка загрузки аккаунта: {username}")
            else:
                logger.warning(f"WARN: Пропущена секция {section} (нет username или shared_secret)")
        
        logger.info(f"STATS: Всего загружено аккаунтов: {loaded_count}")
        return loaded_count > 0
        
    except Exception as e:
        logger.error(f"ERROR: Ошибка загрузки shared_secrets: {e}")
        return False

if __name__ == "__main__":
    print("Loading shared_secret accounts...")
    print("=" * 50)
    
    success = load_shared_secrets()
    
    if success:
        print("\nSUCCESS: Accounts loaded successfully!")
        print("\nNext steps:")
        print("1. Restart the bot")
        print("2. Test command !code")
        print("3. Verify codes are generated")
    else:
        print("\nERROR: Failed to load accounts")
        print("Check:")
        print("1. File configs/shared_secrets.cfg exists")
        print("2. File contains correct data")
        print("3. shared_secret is valid (base64 format)")
