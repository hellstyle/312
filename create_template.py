#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для создания новых шаблонов для FunPay Cardinal.

Использование:
    python create_template.py <template_name> <description>
"""

import os
import sys
from pathlib import Path

# Устанавливаем кодировку UTF-8 для Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')


def create_template(template_name, description=""):
    """
    Создает новый шаблон с базовой структурой.
    
    Args:
        template_name (str): Название шаблона
        description (str): Описание шаблона
    """
    # Определяем пути
    script_dir = Path(__file__).parent
    templates_dir = script_dir / "templates"
    template_dir = templates_dir / template_name
    
    # Проверяем существование шаблона
    if template_dir.exists():
        print(f"❌ Шаблон '{template_name}' уже существует!")
        return False
    
    # Создаем директорию шаблона
    template_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"📁 Создание шаблона '{template_name}'...")
    print()
    
    # Создаем базовый auto_response.cfg
    auto_response_content = f"""[!help]
response: ℹ️ Доступные команды:
!help - эта справка
telegramNotification: 0
enabled: 1

[помощь]
response: ℹ️ Доступные команды:
!help - эта справка
telegramNotification: 0
enabled: 1
"""
    
    auto_response_file = template_dir / "auto_response.cfg"
    with open(auto_response_file, 'w', encoding='utf-8') as f:
        f.write(auto_response_content)
    print(f"✅ Создан файл: auto_response.cfg")
    
    # Создаем базовый auto_delivery.cfg
    auto_delivery_content = f"""[DEFAULT]
response: 🎉 Спасибо за покупку, $username!

📦 Товар: $lot_name
$product

📞 Нужна помощь? Напишите в поддержку!
"""
    
    auto_delivery_file = template_dir / "auto_delivery.cfg"
    with open(auto_delivery_file, 'w', encoding='utf-8') as f:
        f.write(auto_delivery_content)
    print(f"✅ Создан файл: auto_delivery.cfg")
    
    # Создаем README.md
    readme_content = f"""# Шаблон {template_name}

{description if description else 'Описание шаблона'}

## Особенности шаблона

### Автоответы
- `!help` / `помощь` - список доступных команд

### Автовыдача товаров
- Базовая настройка автовыдачи

## Переменные

- `$username` - имя покупателя
- `$lot_name` - название лота/товара
- `$product` - данные товара

## Применение шаблона

### Вариант 1: Ручное копирование
1. Скопируйте содержимое `auto_response.cfg` в ваш файл `configs/auto_response.cfg`
2. Скопируйте содержимое `auto_delivery.cfg` в ваш файл `configs/auto_delivery.cfg`
3. Перезапустите бота

### Вариант 2: Автоматическое применение
```bash
python apply_template.py {template_name}
```

## Настройка после применения

1. Редактируйте команды автоответа в `configs/auto_response.cfg`
2. Настраивайте ответы автовыдачи в `configs/auto_delivery.cfg`
3. Создайте файлы с товарами через Telegram бота
4. Привяжите лоты к автовыдаче через Telegram бота

## Советы

- Используйте переменные для персонализации ответов
- Тестируйте автоответы перед продажей
- Включайте уведомления в Telegram для контроля
"""
    
    readme_file = template_dir / "README.md"
    with open(readme_file, 'w', encoding='utf-8') as f:
        f.write(readme_content)
    print(f"✅ Создан файл: README.md")
    
    print()
    print(f"🎉 Шаблон '{template_name}' успешно создан!")
    print()
    print("📋 Следующие шаги:")
    print(f"1. Отредактируйте файлы в templates/{template_name}/")
    print("2. Добавьте команды автоответа в auto_response.cfg")
    print("3. Настройте автовыдачу в auto_delivery.cfg")
    print("4. Обновите описание в README.md")
    print(f"5. Примените шаблон: python apply_template.py {template_name}")
    
    return True


def main():
    """Главная функция скрипта."""
    if len(sys.argv) < 2:
        print("🚀 Скрипт для создания новых шаблонов FunPay Cardinal")
        print()
        print("Использование:")
        print("  python create_template.py <template_name> [description]")
        print()
        print("Пример:")
        print("  python create_template.py my_template 'Мой шаблон для продажи'")
        return
    
    template_name = sys.argv[1]
    description = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
    
    # Валидация названия
    if not template_name.replace("_", "").replace("-", "").isalnum():
        print("❌ Название шаблона должно содержать только буквы, цифры, подчеркивания и дефисы")
        return
    
    success = create_template(template_name, description)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
