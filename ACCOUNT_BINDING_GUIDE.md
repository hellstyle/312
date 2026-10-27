# Руководство по привязке аккаунтов к боту

Подробное руководство по добавлению и управлению аккаунтами с shared_secret для генерации Guard кодов.

## 🎯 Общий принцип

Для работы оффлайн активации боту нужно знать:
1. **shared_secret** - секретный ключ из maFile аккаунта
2. **Идентификатор пользователя** - как определить, для какого аккаунта генерировать код
3. **Связь с лотом** - какой лот соответствует какому аккаунту

## 📁 Шаг 1: Получение shared_secret из maFiles

### Для Steam:

1. **Найдите директорию Steam:**
   - Windows: `%ProgramFiles%\Steam\config\`
   - macOS: `~/Library/Application Support/Steam/config/`
   - Linux: `~/.steam/config/`

2. **Найдите maFile:**
   - Файлы имеют формат: `12345678901234567.ma`
   - Это Steam ID64 аккаунта

3. **Откройте maFile в текстовом редакторе:**
   ```json
   {
     "account_name": "имя_аккаунта",
     "shared_secret": "wJ4RACRjPJXFPEyPWjHQOx+2T28Sj+HyMA4CmC6Y6yM=",
     "identity_secret": "другой_секрет",
     ...
   }
   ```

4. **Скопируйте значение shared_secret** - это строка в base64 формате

### Для других платформ:

**Epic Games:**
- Файл: `~/.config/Epic/EpicGamesLauncher/Saved/Config/Windows/`
- Формат похож на Steam maFiles

**Origin:**
- Файл: `%ProgramData%/Origin/LocalContent/`
- Может требовать дополнительную обработку

**Uplay:**
- Файл: `%ProgramFiles%/Ubisoft Game Launcher/`
- Обычно в формате похожем на Steam

## 🔧 Шаг 2: Добавление аккаунта в систему

### Вариант 1: Через код (прямой способ)

Создайте файл `configs/shared_secrets.cfg`:

```ini
[steam_account_1]
username = account_name
shared_secret = wJ4RACRjPJXFPEyPWjHQOx+2T28Sj+HyMA4CmC6Y6yM=
platform = steam
description = Основной аккаунт для продажи

[epic_account_1]
username = epic_user123
shared_secret = another_shared_secret_here
platform = epic
description = Epic Games аккаунт
```

Затем добавьте загрузку в бота. Откройте `cardinal.py` и добавьте после инициализации:

```python
from shared_secret_guard import get_shared_secret_manager
import configparser

def load_shared_secrets():
    """Загружает shared_secret из конфигурационного файла."""
    try:
        config = configparser.ConfigParser()
        config.read('configs/shared_secrets.cfg')
        
        manager = get_shared_secret_manager()
        
        for section in config.sections():
            username = config[section].get('username')
            shared_secret = config[section].get('shared_secret')
            
            if username and shared_secret:
                manager.add_account(username, shared_secret)
                logger.info(f"Загружен аккаунт {username} из конфига")
    
    except Exception as e:
        logger.error(f"Ошибка загрузки shared_secrets: {e}")

# Добавьте вызов после инициализации guard_handler
load_shared_secrets()
```

### Вариант 2: Через код (программный способ)

В `cardinal.py` добавьте:

```python
from shared_secret_guard import get_shared_secret_manager

# После инициализации системы
manager = get_shared_secret_manager()

# Добавьте ваши аккаунты
manager.add_account("steam_account1", "shared_secret_1")
manager.add_account("epic_account1", "shared_secret_2")
manager.add_account("origin_account1", "shared_secret_3")
```

### Вариант 3: Через переменные в товарах

Создайте файлы с товарами в формате:

```
# storage/products/steam_accounts.txt
username:account_name
shared_secret:wJ4RACRjPJXFPEyPWjHQOx+2T28Sj+HyMA4CmC6Y6yM=
platform:steam
```

Затем модифицируйте обработку автовыдачи для автоматического добавления аккаунтов.

## 🔗 Шаг 3: Связь аккаунтов с лотами

### Вариант 1: Через название лота

В `configs/auto_delivery.cfg` используйте названия, соответствующие аккаунтам:

```ini
[account_name]
response: 🎮 Спасибо за покупку аккаунта $username!

📝 ДАННЫЕ ДОСТУПА:
$product

🔐 Для Guard кода отправьте: !code
```

При создании лота на FunPay используйте точное название `account_name`.

### Вариант 2: Через специальные поля

Модифицируйте файлы с товарами:

```
# storage/products/steam_lots.txt
lot_name:Steam аккаунт Premium
account:account_name
shared_secret:wJ4RACRjPJXFPEyPWjHQOx+2T28Sj+HyMA4CmC6Y6yM=
login:steam_login
password:steam_password
```

### Вариант 3: Через конфигурацию лота

В `configs/auto_delivery.cfg` добавьте специальные поля:

```ini
[Steam аккаунт Premium]
response: 🎮 Спасибо за покупку, $username!

📝 ДАННЫЕ ДОСТУПА:
$product

🔐 Для Guard кода отправьте: !code
guard_account = account_name
```

Затем модифицируйте `guard_handler.py` для чтения этого поля.

## 🎨 Шаг 4: Адаптация Guard системы

### Модификация для определения аккаунта

Создайте файл `guard_account_mapper.py`:

```python
"""
Модуль для определения нужного аккаунта по лоту или заказу.
"""

class GuardAccountMapper:
    """Определяет какой аккаунт использовать для Guard кода."""
    
    def __init__(self):
        self.lot_to_account = {}  # {lot_name: account_name}
        self.order_to_account = {}  # {order_id: account_name}
    
    def add_mapping(self, lot_name: str, account_name: str):
        """Добавляет соответствие лота аккаунту."""
        self.lot_to_account[lot_name] = account_name
    
    def get_account_for_lot(self, lot_name: str) -> str:
        """Получает имя аккаунта для лота."""
        return self.lot_to_account.get(lot_name)
    
    def get_account_for_order(self, order_id: str) -> str:
        """Получает имя аккаунта для заказа."""
        return self.order_to_account.get(order_id)
    
    def set_order_account(self, order_id: str, account_name: str):
        """Устанавливает аккаунт для конкретного заказа."""
        self.order_to_account[order_id] = account_name


# Глобальный экземпляр
_account_mapper: GuardAccountMapper = None

def init_guard_account_mapper() -> GuardAccountMapper:
    """Инициализация маппера."""
    global _account_mapper
    _account_mapper = GuardAccountMapper()
    return _account_mapper

def get_guard_account_mapper() -> GuardAccountMapper:
    """Получение маппера."""
    return _account_mapper
```

### Интеграция с guard_handler.py

Модифицируйте `guard_handler.py`:

```python
from guard_account_mapper import get_guard_account_mapper

class GuardHandler:
    def __init__(self, cardinal: 'Cardinal'):
        self.cardinal = cardinal
        self.gmail_integration = None
        self.shared_secret_manager = None
        self.account_mapper = get_guard_account_mapper()
        self.enabled = False
        self.use_shared_secret = False
    
    def get_guard_code_for_account(self, account_name: str) -> str:
        """Получает Guard код для конкретного аккаунта."""
        if self.shared_secret_manager:
            code_info = self.shared_secret_manager.get_code_with_info(account_name)
            if code_info and code_info['valid']:
                return code_info['code']
        return None
```

## 📱 Шаг 5: Управление через Telegram бота

### Создание плагина для управления

Создайте файл `plugins/guard_manager.py`:

```python
"""
Плагин для управления Guard аккаунтами через Telegram.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cardinal import Cardinal

from tg_bot import utils, keyboards, CBT
from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B, Message, CallbackQuery
from shared_secret_guard import get_shared_secret_manager
import logging

logger = logging.getLogger("GuardManagerPlugin")

def init_guard_manager(cardinal: Cardinal, *args):
    """Инициализация плагина управления Guard."""
    tg = cardinal.telegram
    bot = tg.bot
    
    def show_guard_menu(c: CallbackQuery):
        """Показывает меню управления Guard."""
        manager = get_shared_secret_manager()
        accounts = manager.list_accounts()
        
        text = f"🔐 Управление Guard аккаунтами\n\n📊 Всего аккаунтов: {len(accounts)}"
        
        keyboard = K()
        if accounts:
            for account in accounts[:5]:  # Показываем первые 5
                keyboard.row(B(account, callback_data=f"guard:select:{account}"))
        
        keyboard.row(B("➕ Добавить аккаунт", callback_data="guard:add"))
        keyboard.row(B("🔄 Обновить", callback_data="guard:refresh"))
        keyboard.row(B("⬅️ Назад", callback_data="menu:main"))
        
        bot.edit_message_text(c.message.chat.id, text, reply_markup=keyboard)
        bot.answer_callback_query(c.id)
    
    def add_account_prompt(c: CallbackQuery):
        """Запрашивает данные для добавления аккаунта."""
        bot.edit_message_text(
            c.message.chat.id,
            "📝 Введите данные аккаунта в формате:\n"
            "имя_аккаунта:shared_secret\n\n"
            "Пример:\n"
            "steam_user:wJ4RACRjPJXFPEyPWjHQOx+2T28Sj+HyMA4CmC6Y6yM=",
            reply_markup=K().row(B("⬅️ Отмена", callback_data="guard:menu"))
        )
        bot.answer_callback_query(c.id)
    
    # Регистрация хендлеров
    tg.cbq_handler(show_guard_menu, lambda c: c.data.startswith("guard:menu"))
    tg.cbq_handler(add_account_prompt, lambda c: c.data == "guard:add")
    
    logger.info("Плагин управления Guard инициализирован")

BIND_TO_PRE_INIT = [init_guard_manager]
```

## 🗂️ Шаг 6: Организация структуры аккаунтов

### Рекомендуемая структура:

```
configs/
├── shared_secrets.cfg          # Основной файл с аккаунтами
├── auto_delivery.cfg           # Настройки автовыдачи
└── auto_response.cfg           # Автоответы

storage/products/
├── steam_accounts.txt          # Товары Steam с привязкой
├── epic_accounts.txt           # Товары Epic с привязкой
└── origin_accounts.txt         # Товары Origin с привязкой
```

### Пример shared_secrets.cfg:

```ini
[Steam_Premium_1]
username = steam_premium_1
shared_secret = secret1_here
platform = steam
lot_binding = Steam аккаунт Premium

[Steam_Standard_1]
username = steam_standard_1
shared_secret = secret2_here
platform = steam
lot_binding = Steam аккаунт Standard

[Epic_Fortnite_1]
username = epic_fortnite_1
shared_secret = secret3_here
platform = epic
lot_binding = Epic Fortnite аккаунт
```

## 🔍 Шаг 7: Проверка и тестирование

### Тестовый скрипт для проверки привязки

Создайте `test_account_binding.py`:

```python
#!/usr/bin/env python3
"""Тестовый скрипт для проверки привязки аккаунтов."""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from shared_secret_guard import SharedSecretManager, get_shared_secret_manager

def test_account_binding():
    """Тестирует привязку аккаунтов."""
    print("TEST: Account Binding System")
    print("=" * 50)
    
    # Инициализация менеджера
    manager = SharedSecretManager()
    
    # Тестовый shared_secret
    test_secret = "wJ4RACRjPJXFPEyPWjHQOx+2T28Sj+HyMA4CmC6Y6yM="
    
    # Добавление тестовых аккаунтов
    print("\n[TEST 1] Adding test accounts")
    manager.add_account("steam_account1", test_secret)
    manager.add_account("epic_account1", test_secret)
    print("Added 2 test accounts")
    
    # Проверка списка
    print("\n[TEST 2] List accounts")
    accounts = manager.list_accounts()
    print(f"Accounts: {accounts}")
    assert len(accounts) == 2, "Should have 2 accounts"
    
    # Генерация кода для конкретного аккаунта
    print("\n[TEST 3] Generate code for specific account")
    code = manager.get_code("steam_account1")
    print(f"Code for steam_account1: {code}")
    assert code is not None, "Should generate code"
    
    # Проверка несуществующего аккаунта
    print("\n[TEST 4] Get code for non-existent account")
    code_none = manager.get_code("nonexistent")
    print(f"Code for nonexistent: {code_none}")
    assert code_none is None, "Should return None for nonexistent"
    
    print("\n" + "=" * 50)
    print("[SUCCESS] Account binding tests passed!")

if __name__ == "__main__":
    test_account_binding()
```

## 📋 Шаг 8: Автоматизация при продаже

### Интеграция с процессом продажи

Модифицируйте `handlers.py` для автоматического связывания заказа с аккаунтом:

```python
def deliver_product_handler(c: Cardinal, e: NewOrderEvent, *args) -> None:
    """Обертка для deliver_product() с автоматической привязкой."""
    # ... существующий код ...
    
    # После успешной выдачи товара
    if e.delivered:
        # Определяем аккаунт из названия лота
        from guard_account_mapper import get_guard_account_mapper
        mapper = get_guard_account_mapper()
        
        account_name = mapper.get_account_for_lot(e.order.description)
        if account_name:
            # Связываем заказ с аккаунтом
            mapper.set_order_account(str(e.order.id), account_name)
            logger.info(f"Заказ {e.order.id} связан с аккаунтом {account_name}")
```

## 🎯 Рекомендации по организации

### Для небольших магазинов (1-5 аккаунтов):
- Используйте прямой метод через код
- Храните shared_secret в `configs/shared_secrets.cfg`
- Привязывайте аккаунты через названия лотов

### Для средних магазинов (5-20 аккаунтов):
- Используйте конфигурационные файлы
- Создайте систему маппинга лотов к аккаунтам
- Используйте плагин для управления через Telegram

### Для крупных магазинов (20+ аккаунтов):
- Используйте базу данных для хранения
- Создайте систему автоматического распределения
- Используйте веб-интерфейс для управления
- Реализуйте систему ротации аккаунтов

## 🔒 Безопасность при хранении

### Рекомендации:
1. **Шифрование shared_secret** - храните в зашифрованном виде
2. **Ограничение доступа** - только администраторы имеют доступ
3. **Регулярная смена** - меняйте shared_secret периодически
4. **Резервные копии** - храните зашифрованные бэкапы
5. **Логирование** - логируйте все операции с аккаунтами

### Пример шифрования:

```python
from cryptography.fernet import Fernet

# Шифрование shared_secret
key = Fernet.generate_key()
cipher_suite = Fernet(key)

encrypted_secret = cipher_suite.encrypt(b"shared_secret_here")
decrypted_secret = cipher_suite.decrypt(encrypted_secret)
```

## 📊 Мониторинг использования

### Создание системы отчетов:

```python
def generate_account_usage_report():
    """Генерирует отчет об использовании аккаунтов."""
    manager = get_shared_secret_manager()
    
    report = {
        'total_accounts': len(manager.list_accounts()),
        'active_accounts': 0,  # Подсчет активных
        'code_generations': 0,  # Подсчет генераций
        'errors': 0  # Подсчет ошибок
    }
    
    return report
```

## 🚀 Полный процесс настройки

### Итоговый чек-лист:

1. [ ] Получите shared_secret из maFiles
2. [ ] Создайте файл `configs/shared_secrets.cfg`
3. [ ] Добавьте аккаунты в конфиг
4. [ ] Модифицируйте `cardinal.py` для загрузки конфига
5. [ ] Настройте связь лотов с аккаунтами
6. [ ] Протестируйте генерацию кодов
7. [ ] Настройте Telegram управление (опционально)
8. [ ] Реализуйте шифрование (рекомендуется)
9. [ ] Настройте мониторинг и логирование
10. [ ] Протестируйте полный цикл продажи

## 💡 Пример полного цикла

### Сценарий продажи Steam аккаунта:

1. **Настройка:**
   - Получили shared_secret из maFile
   - Добавили в `configs/shared_secrets.cfg`
   - Создали лот "Steam аккаунт Premium"

2. **Продажа:**
   - Покупатель оформляет заказ
   - Бот автоматически выдает логин/пароль
   - Система связывает заказ с аккаунтом

3. **Активация:**
   - Покупатель отправляет `!code`
   - Бот определяет нужный аккаунт
   - Генерирует код через shared_secret
   - Покупатель вводит код в Steam

4. **Завершение:**
   - Аккаунт активирован
   - Покупатель играет в оффлайн режиме
   - Все действия залогированы

---

**FunPay Cardinal** - простой и эффективный бот для автоматизации FunPay

📞 Поддержка: https://t.me/funpay_cardinal
