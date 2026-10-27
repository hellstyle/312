#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для применения шаблонов к конфигурации FunPay Cardinal.

Использование:
    python apply_template.py <template_name>

Доступные шаблоны:
    steam_guard - Steam Guard коды и аккаунты
    digital_games - Цифровые игры и ключи
    game_currency - Игровая валюта и предметы
"""

import os
import sys
import shutil
from pathlib import Path

# Устанавливаем кодировку UTF-8 для Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')


def apply_template(template_name):
    """
    Применяет указанный шаблон к конфигурации бота.
    
    Args:
        template_name (str): Название шаблона
    """
    # Определяем пути
    script_dir = Path(__file__).parent
    templates_dir = script_dir / "templates"
    template_dir = templates_dir / template_name
    configs_dir = script_dir / "configs"
    
    # Проверяем существование шаблона
    if not template_dir.exists():
        print(f"❌ Шаблон '{template_name}' не найден!")
        print(f"📁 Доступные шаблоны:")
        for item in templates_dir.iterdir():
            if item.is_dir() and not item.name.startswith('_'):
                print(f"   • {item.name}")
        return False
    
    # Проверяем существование конфигурационных файлов
    auto_response_src = template_dir / "auto_response.cfg"
    auto_delivery_src = template_dir / "auto_delivery.cfg"
    auto_response_dst = configs_dir / "auto_response.cfg"
    auto_delivery_dst = configs_dir / "auto_delivery.cfg"
    
    if not auto_response_src.exists():
        print(f"❌ В шаблоне отсутствует файл auto_response.cfg")
        return False
    
    if not auto_delivery_src.exists():
        print(f"❌ В шаблоне отсутствует файл auto_delivery.cfg")
        return False
    
    # Создаем резервные копии существующих конфигов
    timestamp = ""
    try:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    except:
        pass
    
    backup_suffix = f".backup_{timestamp}" if timestamp else ".backup"
    
    print(f"🔄 Применение шаблона '{template_name}'...")
    print()
    
    # Резервное копирование auto_response.cfg
    if auto_response_dst.exists():
        backup_file = configs_dir / f"auto_response.cfg{backup_suffix}"
        shutil.copy2(auto_response_dst, backup_file)
        print(f"✅ Создана резервная копия: {backup_file.name}")
    
    # Резервное копирование auto_delivery.cfg
    if auto_delivery_dst.exists():
        backup_file = configs_dir / f"auto_delivery.cfg{backup_suffix}"
        shutil.copy2(auto_delivery_dst, backup_file)
        print(f"✅ Создана резервная копия: {backup_file.name}")
    
    print()
    
    # Копирование файлов шаблона
    try:
        shutil.copy2(auto_response_src, auto_response_dst)
        print(f"✅ Обновлен файл: configs/auto_response.cfg")
        
        shutil.copy2(auto_delivery_src, auto_delivery_dst)
        print(f"✅ Обновлен файл: configs/auto_delivery.cfg")
        
        print()
        print(f"🎉 Шаблон '{template_name}' успешно применен!")
        print()
        print("📋 Следующие шаги:")
        print("1. Проверьте и отредактируйте конфигурационные файлы при необходимости")
        print("2. Перезапустите бота")
        print("3. Настройте файлы с товарами через Telegram бота")
        print("4. Привяжите лоты к автовыдаче через Telegram бота")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при копировании файлов: {e}")
        return False


def list_templates():
    """Выводит список доступных шаблонов."""
    script_dir = Path(__file__).parent
    templates_dir = script_dir / "templates"
    
    print("📋 Доступные шаблоны:")
    print()
    
    if not templates_dir.exists():
        print("❌ Директория templates не найдена")
        return
    
    for item in templates_dir.iterdir():
        if item.is_dir() and not item.name.startswith('_'):
            readme_file = item / "README.md"
            description = ""
            if readme_file.exists():
                try:
                    with open(readme_file, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        for line in lines:
                            if line.strip().startswith('#'):
                                continue
                            if line.strip():
                                description = line.strip()
                                break
                except:
                    pass
            
            print(f"📁 {item.name}")
            if description:
                print(f"   {description}")
            print()


def main():
    """Главная функция скрипта."""
    if len(sys.argv) < 2:
        print("🚀 Скрипт для применения шаблонов FunPay Cardinal")
        print()
        print("Использование:")
        print("  python apply_template.py <template_name>")
        print("  python apply_template.py --list")
        print()
        list_templates()
        return
    
    template_name = sys.argv[1]
    
    if template_name == "--list" or template_name == "-l":
        list_templates()
        return
    
    if template_name == "--help" or template_name == "-h":
        print("🚀 Скрипт для применения шаблонов FunPay Cardinal")
        print()
        print("Использование:")
        print("  python apply_template.py <template_name>  - применить шаблон")
        print("  python apply_template.py --list           - список шаблонов")
        print("  python apply_template.py --help           - эта справка")
        print()
        list_templates()
        return
    
    success = apply_template(template_name)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
