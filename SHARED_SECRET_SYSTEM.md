# Система Shared Secret Guard

FunPay Cardinal включает усовершенствованную систему Guard кодов на основе shared_secret, которая является более надежной и быстрой альтернативой Gmail.

## 🎯 Преимущества Shared Secret

### По сравнению с Gmail:
- ✅ **Мгновенная генерация** - коды генерируются локально за миллисекунды
- ✅ **Не требует Gmail** - работает без почтового сервиса
- ✅ **Надежнее** - нет зависимости от интернет-соединения и почтового сервера
- ✅ **Универсальность** - работает для всех платформ (Steam, Epic, Origin и т.д.)
- ✅ **Безопаснее** - коды не передаются через интернет
- ✅ **Проще настройка** - нужен только shared_secret из maFiles

## 🔧 Как получить Shared Secret

### Из Steam maFiles:
1. Найдите файл `maFiles` в директории Steam:
   - Windows: `%ProgramFiles%\Steam\config\`
   - macOS: `~/Library/Application Support/Steam/config/`
   - Linux: `~/.steam/config/`

2. Откройте нужный maFile (например, `12345678901234567.ma`)

3. Найдите поле `"shared_secret"` - это base64 закодированная строка

4. Скопируйте значение shared_secret

### Из других платформ:
Для Epic Games, Origin, Uplay и других платформ процесс аналогичен - ищите файлы конфигурации с shared_secret.

## 🚀 Использование системы

### Базовое использование:

```python
from shared_secret_guard import SharedSecretGuard

# Создаем Guard с shared_secret
guard = SharedSecretGuard("wJ4RACRjPJXFPEyPWjHQOx+2T28Sj+HyMA4CmC6Y6yM=")

# Генерируем код
code = guard.generate_code()
print(f"Код: {code}")  # например: 123456

# Получаем код с информацией о времени
code, remaining = guard.get_code_with_time_remaining()
print(f"Код: {code}, Осталось: {remaining} секунд")
```

### Использование с менеджером:

```python
from shared_secret_guard import SharedSecretManager

# Создаем менеджер
manager = SharedSecretManager()

# Добавляем аккаунты
manager.add_account("steam_user1", "shared_secret_1")
manager.add_account("epic_user2", "shared_secret_2")

# Получаем код для конкретного пользователя
code = manager.get_code("steam_user1")
print(f"Код для steam_user1: {code}")

# Получаем код с информацией
code_info = manager.get_code_with_info("epic_user2")
print(f"Код: {code_info['code']}, Время: {code_info['remaining_seconds']} сек")
```

## 🔌 Интеграция с FunPay Cardinal

### Автоматическая интеграция:

Система автоматически интегрирована через `guard_handler.py`:

```python
# В cardinal.py автоматически инициализируется
from shared_secret_guard import init_shared_secret_manager
init_shared_secret_manager()

# В guard_handler.py автоматически используется
guard_handler.init_shared_secret()
guard_handler.add_shared_secret_account("username", "shared_secret")
```

### Приоритет системы:
1. **Shared Secret** - используется если доступен
2. **Gmail** - используется как fallback если shared_secret не сработал
3. **Конфиг автоответов** - используется если ни один метод не доступен

## 📋 Добавление аккаунтов

### Через код:

```python
from shared_secret_guard import get_shared_secret_manager

manager = get_shared_secret_manager()
manager.add_account("username", "shared_secret_here")
```

### Через конфигурационный файл:

Создайте файл `configs/shared_secrets.cfg`:
```ini
[steam_account1]
username = account_name
shared_secret = wJ4RACRjPJXFPEyPWjHQOx+2T28Sj+HyMA4CmC6Y6yM=

[epic_account1]
username = epic_user
shared_secret = another_shared_secret_here
```

### Через Telegram бота:

Можно добавить плагин для управления аккаунтами через Telegram.

## 🎨 Для разных платформ

### Steam:
```python
manager.add_account("steam_user", "steam_shared_secret")
```

### Epic Games:
```python
manager.add_account("epic_user", "epic_shared_secret")
```

### Origin:
```python
manager.add_account("origin_user", "origin_shared_secret")
```

### Uplay:
```python
manager.add_account("uplay_user", "uplay_shared_secret")
```

## 🔒 Безопасность

### Хранение shared_secret:
- Храните shared_secret в безопасном месте
- Не передавайте его третьим лицам
- Используйте шифрование при хранении в файлах
- Регулярно меняйте shared_secret при подозрении компрометации

### Защита системы:
- Коды генерируются локально
- Не передаются через интернет
- Имеют ограниченное время действия (30 секунд)
- Требуют подтверждения покупки

## 🧪 Тестирование

Запустите тестовый скрипт:

```bash
python test_shared_secret.py
```

Скрипт проверяет:
- Создание Guard с shared_secret
- Генерацию кодов
- Получение кодов с информацией о времени
- Валидацию кодов
- Работу менеджера аккаунтов
- Добавление и удаление аккаунтов

## 📊 Мониторинг

### Логи системы:

```bash
# Логи Shared Secret Guard
tail -f logs/SharedSecretGuard.log
tail -f logs/GuardHandler.log
```

### Статистика:

Система автоматически логирует:
- Все генерации кодов
- Успешные и неудачные попытки
- Ошибки генерации
- Время генерации кодов

## 🛠️ Устранение проблем

### Shared Secret не работает
1. Проверьте, что shared_secret корректный (base64 формат)
2. Убедитесь, что он из правильного maFile
3. Проверьте логи на наличие ошибок
4. Попробуйте сгенерировать код вручную через тестовый скрипт

### Код не генерируется
1. Проверьте, что аккаунт добавлен в менеджер
2. Убедитесь, что shared_secret валидный
3. Проверьте синхронизацию времени на сервере
4. Проверьте логи системы

### Проблемы с интеграцией
1. Убедитесь, что модуль импортирован в cardinal.py
2. Проверьте, что guard_handler инициализирован правильно
3. Проверьте логи при запуске бота
4. Убедитесь, что система включена (enabled = True)

## 💡 Советы по использованию

### Для максимальной надежности:
- Используйте shared_secret как основной метод
- Gmail оставьте как fallback
- Регулярно проверяйте логи
- Имейте резервные shared_secret

### Для разных платформ:
- Создайте отдельные shared_secret для каждой платформы
- Используйте понятные имена аккаунтов
- Документируйте какой shared_secret для какой платформы

### Для безопасности:
- Храните shared_secret в зашифрованном виде
- Ограничьте доступ к файлам конфигурации
- Регулярно обновляйте shared_secret
- Используйте разные аккаунты для разных платформ

## 🔄 Миграция с Gmail на Shared Secret

### Шаги миграции:

1. **Получите shared_secret** из maFiles ваших аккаунтов
2. **Добавьте аккаунты** в систему shared_secret
3. **Протестируйте** генерацию кодов
4. **Отключите Gmail** (если хотите полностью перейти)
5. **Мониторьте** работу системы в течение нескольких дней

### Преимущества миграции:
- Мгновенная генерация кодов
- Нет зависимости от почты
- Более надежная работа
- Поддержка всех платформ

## 📚 Сравнение методов

| Метод | Скорость | Надежность | Настройка | Платформы |
|-------|---------|------------|-----------|-----------|
| Shared Secret | Мгновенно | Высокая | Простая | Все |
| Gmail | 15-30 сек | Средняя | Средняя | Steam |

## 🎯 Рекомендации

### Для новых установок:
- Используйте только shared_secret
- Не настраивайте Gmail если не нужно

### Для существующих установок:
- Добавьте shared_secret как основной метод
- Оставьте Gmail как fallback
- Постепенно переходите на shared_secret

### Для критически важных систем:
- Используйте только shared_secret
- Имейте резервные shared_secret
- Регулярно тестируйте систему

## 📞 Поддержка

При проблемах с shared_secret:
1. Проверьте документацию Steam по maFiles
2. Используйте тестовый скрипт для диагностики
3. Проверьте логи системы
4. Обратитесь в Telegram чат поддержки

---

**FunPay Cardinal** - простой и эффективный бот для автоматизации FunPay

📞 Поддержка: https://t.me/funpay_cardinal
