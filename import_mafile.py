#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Импорт аккаунта из maFile SteamDesktopAuthenticator в FunPayCardinal.

Что делает:
  1. Читает manifest.json и зашифрованный *.maFile из папки SDA (maFiles).
  2. Расшифровывает maFile форматом SDA (PBKDF2-SHA1 / AES-256-CBC / PKCS7),
     используя passkey, который вы задали в Steam Desktop Authenticator.
  3. Достаёт account_name и shared_secret.
  4. Дописывает (или обновляет) секцию в configs/shared_secrets.cfg.
  5. Генерирует тестовый Steam Guard код для проверки.

Passkey можно передать:
  - аргументом --passkey "ваш_пароль"
  - переменной окружения FPC_PASSKEY
  - интерактивным вводом (getpass, без эха на экран) — самый приватный способ.

Пример запуска (интерактивно, passkey не светится):
    .venv\\Scripts\\python.exe import_mafile.py --mafiles "C:\\Users\\basbo\\Desktop\\SteamDesktopAuthenticator-master\\maFiles"
"""

import sys
import os
import json
import base64
import argparse
import hashlib
import configparser
import getpass

# UTF-8 в консоли Windows
if sys.platform == "win32":
    import codecs
    try:
        sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
        sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "strict")
    except Exception:
        pass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.exceptions import InvalidTag  # noqa: F401  (не для CBC, но на случай будущих правок)


# --- Параметры шифрования SDA (см. FileEncryptor.cs) ---
PBKDF2_ITERATIONS = 50000
KEY_SIZE_BYTES = 32
# SALT_LENGTH = 8, IV_LENGTH = 16 — берутся из manifest

# Кандидаты кодировки passkey. .NET Rfc2898DeriveBytes со строковым конструктором
# исторически использует UTF-8, но чтобы не гадать — пробуем по порядку и
# проверяем результат валидностью JSON с shared_secret.
PASSWORD_ENCODINGS = ["utf-8", "utf-16-le", "utf-16-be", "latin-1"]


def derive_key(passkey: str, salt: bytes, encoding: str) -> bytes:
    """PBKDF2-HMAC-SHA1, как в SDA (Rfc2898DeriveBytes, 50000 итераций, 32 байта)."""
    return hashlib.pbkdf2_hmac("sha1", passkey.encode(encoding), salt, PBKDF2_ITERATIONS, dklen=KEY_SIZE_BYTES)


def decrypt_mafile(ciphertext: bytes, salt: bytes, iv: bytes, passkey: str):
    """
    Расшифровывает maFile. Пробует несколько кодировок passkey и возвращает
    (plaintext_json: dict, used_encoding: str) либо (None, None).
    """
    for enc in PASSWORD_ENCODINGS:
        try:
            key = derive_key(passkey, salt, enc)
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
            decryptor = cipher.decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()

            unpadder = sym_padding.PKCS7(algorithms.AES.block_size).unpadder()
            plaintext = unpadder.update(padded) + unpadder.finalize()
        except Exception:
            # Неверный ключ/кодировка -> обычно падает на unpad
            continue

        try:
            data = json.loads(plaintext.decode("utf-8"))
        except Exception:
            continue

        if isinstance(data, dict) and "shared_secret" in data:
            return data, enc
    return None, None


def load_manifest(mafiles_dir: str) -> dict:
    manifest_path = os.path.join(mafiles_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"manifest.json не найден в {mafiles_dir}")
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_mafile(mafiles_dir: str, filename: str) -> bytes:
    path = os.path.join(mafiles_dir, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"maFile не найден: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return base64.b64decode(f.read().strip())


def update_shared_secrets_config(config_path: str, account_name: str, shared_secret: str, steamid: str) -> bool:
    """Дописает или обновляет секцию [account_name] в shared_secrets.cfg."""
    config = configparser.ConfigParser()
    # Сохраняем существующие секции
    if os.path.exists(config_path):
        config.read(config_path, encoding="utf-8")

    changed = False
    if account_name in config.sections():
        old = config[account_name].get("shared_secret", "")
        if old == shared_secret:
            print(f"  • Секция [{account_name}] уже существует с таким же shared_secret — оставлено как есть.")
            return False
        print(f"  • Секция [{account_name}] существует — обновляю shared_secret.")
        changed = True
    else:
        config[account_name] = {}
        changed = True

    config[account_name]["username"] = account_name
    config[account_name]["shared_secret"] = shared_secret
    config[account_name]["platform"] = "steam"
    config[account_name]["description"] = f"Импортировано из maFile (steamid {steamid})"

    with open(config_path, "w", encoding="utf-8") as f:
        config.write(f)
    return changed


def test_code(shared_secret: str):
    """Генерирует Steam Guard код из shared_secret для проверки."""
    try:
        from shared_secret_guard import SharedSecretGuard
        guard = SharedSecretGuard(shared_secret)
        if not guard.enabled:
            print("  • Не удалось инициализировать генератор кода (неверный shared_secret).")
            return
        code, remaining = guard.get_code_with_time_remaining()
        if code:
            print(f"  • Тестовый код: {code}  (действителен ещё ~{remaining} сек)")
        else:
            print("  • Код сгенерировать не удалось.")
    except Exception as e:
        print(f"  • Ошибка генерации тестового кода: {e}")


def main():
    parser = argparse.ArgumentParser(description="Импорт maFile SDA в FunPayCardinal")
    parser.add_argument("--mafiles", default=r"C:\Users\basbo\Desktop\SteamDesktopAuthenticator-master\maFiles",
                        help="Путь к папке maFiles из SteamDesktopAuthenticator")
    parser.add_argument("--passkey", default=None,
                        help="Passkey шифрования SDA (лучше вводить интерактивно/env)")
    parser.add_argument("--config", default="configs/shared_secrets.cfg",
                        help="Путь к shared_secrets.cfg")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать результат, не записывать в конфиг")
    args = parser.parse_args()

    # Определяем passkey
    passkey = args.passkey or os.environ.get("FPC_PASSKEY")
    if not passkey:
        print("maFile зашифрован SteamDesktopAuthenticator. Нужен passkey (пароль,")
        print("который вы задали в SDA при создании аутентификатора).")
        try:
            passkey = getpass.getpass("Passkey (ввод скрыт): ")
        except Exception:
            passkey = input("Passkey: ")
    if not passkey:
        print("Passkey не указан. Выход.")
        return 1

    print("=" * 60)
    print("Импорт maFile из:", args.mafiles)
    print("=" * 60)

    try:
        manifest = load_manifest(args.mafiles)
    except Exception as e:
        print(f"Ошибка чтения manifest: {e}")
        return 1

    if not manifest.get("encrypted", False):
        print("manifest.json сообщает, что файлы НЕ зашифрованы — расшифровка не нужна.")

    entries = manifest.get("entries", [])
    if not entries:
        print("В manifest нет записей (entries). Нечего импортировать.")
        return 1

    total_ok = 0
    for entry in entries:
        filename = entry.get("filename")
        steamid = str(entry.get("steamid", ""))
        salt = base64.b64decode(entry["encryption_salt"])
        iv = base64.b64decode(entry["encryption_iv"])

        print(f"\n[{filename}]  steamid={steamid}")

        try:
            ciphertext = read_mafile(args.mafiles, filename)
        except Exception as e:
            print(f"  • {e}")
            continue

        data, used_enc = decrypt_mafile(ciphertext, salt, iv, passkey)
        if data is None:
            print("  • НЕ удалось расшифровать. Проверьте passkey (пароль SDA).")
            continue

        account_name = data.get("account_name") or steamid
        shared_secret = data.get("shared_secret")
        if not shared_secret:
            print("  • shared_secret отсутствует в maFile — импорт невозможен.")
            continue

        print(f"  • Расшифровано (кодировка passkey: {used_enc}).")
        print(f"  • account_name: {account_name}")
        print(f"  • shared_secret: {shared_secret}")
        print(f"  • steamid: {data.get('steamid', steamid)}")

        test_code(shared_secret)

        if not args.dry_run:
            try:
                changed = update_shared_secrets_config(args.config, account_name, shared_secret, steamid)
                if changed or True:
                    print(f"  • Конфиг обновлён: {args.config}")
            except Exception as e:
                print(f"  • Ошибка записи в конфиг: {e}")
        else:
            print("  • --dry-run: запись в конфиг пропущена.")

        total_ok += 1

    print("\n" + "=" * 60)
    print(f"Успешно обработано аккаунтов: {total_ok}")
    if total_ok:
        print("Перезапустите бота, чтобы новый аккаунт подхватился (или выполните")
        print("load_shared_secrets.py, если подгрузка идёт через него).")
    return 0 if total_ok else 1


if __name__ == "__main__":
    sys.exit(main())
