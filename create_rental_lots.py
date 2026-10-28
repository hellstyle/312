#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Создание лотов аренды Steam аккаунтов на FunPay.

Запуск:
    .venv\Scripts\python.exe create_rental_lots.py

Скрипт:
  1. Авторизуется на FunPay по golden_key из конфига.
  2. Находит node_id подкатегории «Аренда аккаунтов» в Steam.
  3. Создаёт лоты из списка GAMES.
  4. Добавляет секции в configs/auto_delivery.cfg для каждого лота.
"""

import sys, os, time, configparser
sys.path.insert(0, os.path.dirname(__file__))

import FunPayAPI

# ──────────────────────────────────────────────────────────────────────────────
# Список игр: (название, цена за час)
# ──────────────────────────────────────────────────────────────────────────────
GAMES = [
    ("RUST",                          80),
    ("Garry's Mod",                   60),
    ("Hearts of Iron IV",             80),
    ("Counter-Strike (Zero/Source)",  60),
    ("Peak",                          60),
    ("Plague Inc: Evolved",           60),
    ("Portal (1 и 2)",                60),
    ("Terraria",                      60),
    ("TheoTown",                      60),
]

RENTAL_HOURS = 1   # цена за 1 час

DESCRIPTION_TEMPLATE = """\
🎮 Аренда аккаунта Steam — {game}

⏱ Время аренды: {hours} ч
💰 Цена: {price} ₽ / час

📋 Условия:
• Оффлайн-активация игры
• Отсчёт времени начинается после скачивания игры
• Поддержка через бота

🔧 После оплаты:
1. Получите данные аккаунта автоматически
2. Войдите в Steam (код — командой !code в чате)
3. Скачайте игру и напишите !downloaded
4. Наслаждайтесь!
"""

PAYMENT_MSG_TEMPLATE = """\
Спасибо за покупку! 🎮

Войдите в аккаунт, выберите игру командой !select_game {game}, скачайте её и напишите !downloaded — тогда начнётся отсчёт времени.
Код Steam Guard: !code
"""

# ──────────────────────────────────────────────────────────────────────────────

def load_golden_key() -> str:
    cfg = configparser.ConfigParser()
    cfg.read("configs/_main.cfg", encoding="utf-8")
    key = cfg.get("FunPay", "golden_key", fallback="")
    if not key:
        raise RuntimeError("golden_key не найден в configs/_main.cfg")
    return key


def find_steam_rental_node(account: FunPayAPI.Account) -> tuple[int, str]:
    """
    Ищет node_id подкатегории аренды Steam.
    Возвращает (node_id, full_name).
    """
    from FunPayAPI.common.enums import SubCategoryTypes
    subcats = account.get_sorted_subcategories()[SubCategoryTypes.COMMON]

    # Ищем подкатегорию, которая содержит «аренда» и «steam» (без учёта регистра)
    candidates = []
    for node_id, subcat in subcats.items():
        name_lower = subcat.fullname.lower()
        if "steam" in name_lower and ("аренд" in name_lower or "rent" in name_lower or "account" in name_lower):
            candidates.append((node_id, subcat.fullname))

    if not candidates:
        # Показываем все Steam подкатегории для выбора
        print("\nПодкатегории со 'Steam' в названии:")
        for node_id, subcat in subcats.items():
            if "steam" in subcat.fullname.lower():
                print(f"  node_id={node_id:6d}  {subcat.fullname}")
        raise RuntimeError(
            "Подкатегория аренды Steam не найдена автоматически. "
            "Скопируйте нужный node_id из списка выше и передайте его: "
            "python create_rental_lots.py --node <node_id>"
        )

    if len(candidates) == 1:
        return candidates[0]

    print("\nНайдено несколько подходящих подкатегорий:")
    for i, (nid, name) in enumerate(candidates):
        print(f"  [{i}] node_id={nid}  {name}")
    choice = int(input("Выберите номер: "))
    return candidates[choice]


def get_existing_lot_titles(account: FunPayAPI.Account) -> set[str]:
    """Возвращает множество названий (title_ru) уже существующих лотов."""
    from FunPayAPI.common.enums import SubCategoryTypes
    profile = account.get_user(account.id)
    titles = set()
    for lot in profile.get_lots():
        if lot.description:
            titles.add(lot.description.strip())
    return titles


def add_to_auto_delivery(game_name: str, price: int):
    """Добавляет секцию в configs/auto_delivery.cfg если её ещё нет."""
    path = "configs/auto_delivery.cfg"
    cfg = configparser.RawConfigParser()
    cfg.read(path, encoding="utf-8")

    section = f"Аренда {game_name}"
    if cfg.has_section(section):
        return False  # уже есть

    cfg.add_section(section)
    cfg.set(section, "enabled", "1")
    cfg.set(section, "response",
            f"🎮 Данные аккаунта для аренды:\n$product\n\n"
            f"⏱ Цена: {price} ₽/час\n"
            f"Выберите игру: !select_game {game_name}\n"
            f"Скачали? Напишите: !downloaded\n"
            f"Код Steam Guard: !code")
    cfg.set(section, "productsFileName", "ACCOUNT_RENTAL.txt")
    cfg.set(section, "disableAutoRestore", "0")

    with open(path, "w", encoding="utf-8") as f:
        cfg.write(f)
    return True


def create_lot(account: FunPayAPI.Account, node_id: int,
               game_name: str, price: int) -> bool:
    """Создаёт один лот на FunPay. Возвращает True при успехе."""
    try:
        lot_fields = account.get_lot_fields(lot_id=0, node_id=node_id)

        title = f"Аренда {game_name}"
        lot_fields.title_ru = title
        lot_fields.title_en = f"{game_name} Account Rental"
        lot_fields.description_ru = DESCRIPTION_TEMPLATE.format(
            game=game_name, hours=RENTAL_HOURS, price=price)
        lot_fields.description_en = (
            f"🎮 Steam account rental — {game_name}\n"
            f"⏱ {RENTAL_HOURS}h | 💰 {price} RUB/h\n\n"
            f"After purchase: login with !code, select game with "
            f"!select_game {game_name}, then !downloaded to start timer."
        )
        lot_fields.payment_msg_ru = PAYMENT_MSG_TEMPLATE.format(game=game_name)
        lot_fields.payment_msg_en = (
            f"Thank you! Login to Steam (use !code for Guard code), "
            f"type !select_game {game_name}, then !downloaded to start."
        )
        lot_fields.price = float(price)
        lot_fields.active = True

        account.save_lot(lot_fields)
        return True

    except Exception as e:
        print(f"  ❌ Ошибка создания лота «{game_name}»: {e}")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", type=int, default=None,
                        help="node_id подкатегории (если не указан — ищется автоматически)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать что будет создано, без реальных запросов")
    args = parser.parse_args()

    print("=" * 60)
    print("Создание лотов аренды Steam аккаунтов на FunPay")
    print("=" * 60)

    golden_key = load_golden_key()
    account = FunPayAPI.Account(golden_key)
    print("Авторизуюсь на FunPay...")
    account.get()
    print(f"Вошёл как: {account.username} (ID: {account.id})")

    # Находим node_id
    if args.node:
        node_id = args.node
        print(f"\nИспользую node_id={node_id} (из аргумента --node)")
    else:
        print("\nИщу подкатегорию аренды Steam...")
        node_id, subcat_name = find_steam_rental_node(account)
        print(f"Найдено: node_id={node_id}  «{subcat_name}»")

    # Существующие лоты (чтобы не дублировать)
    print("\nПолучаю существующие лоты...")
    existing = get_existing_lot_titles(account)
    print(f"Уже существует: {len(existing)} лотов")

    print("\nБуду создавать:")
    to_create = []
    for game, price in GAMES:
        title = f"Аренда {game}"
        if title in existing:
            print(f"  ⏭  «{title}» — уже существует, пропускаю")
        else:
            print(f"  ✚  «{title}» — {price} ₽/ч")
            to_create.append((game, price))

    if not to_create:
        print("\nВсе лоты уже существуют. Ничего создавать не нужно.")
        return

    if args.dry_run:
        print("\n--dry-run: реальных запросов не делаю.")
        return

    print(f"\nСоздаю {len(to_create)} лотов...")
    ok = 0
    for game, price in to_create:
        print(f"  Создаю «Аренда {game}» ({price} ₽/ч)...", end=" ", flush=True)
        if create_lot(account, node_id, game, price):
            add_to_auto_delivery(game, price)
            print("✅")
            ok += 1
        time.sleep(1.5)  # пауза между запросами

    print(f"\n{'='*60}")
    print(f"Создано лотов: {ok}/{len(to_create)}")
    if ok > 0:
        print("Секции добавлены в configs/auto_delivery.cfg")
        print("Перезапустите бота чтобы он подхватил новые лоты.")


if __name__ == "__main__":
    main()
