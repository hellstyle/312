#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для расшифровки Steam maFile.
"""

import sys
import base64
import json

# Устанавливаем кодировку UTF-8 для Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.get_writer('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

try:
    from cryptography.fernet import Fernet
except ImportError:
    print("Installing cryptography...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "cryptography"])
    from cryptography.fernet import Fernet

def decrypt_mafile(encrypted_data: str, password: str) -> dict:
    """
    Расшифровывает maFile Steam.
    
    :param encrypted_data: зашифрованные данные
    :param password: пароль для расшифровки
    :return: расшифрованные данные в формате JSON
    """
    try:
        # Создаем ключ из пароля
        key = Fernet(password.encode())
        
        # Расшифровываем
        decrypted_data = key.decrypt(encrypted_data.encode())
        
        # Декодируем из base64
        decoded_data = base64.b64decode(decrypted_data)
        
        # Парсим JSON
        mafile_data = json.loads(decoded_data)
        
        return mafile_data
        
    except Exception as e:
        print(f"Ошибка расшифровки: {e}")
        return None

# Тестовые данные
encrypted_mafile = """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"""

print("Steam maFile Decryptor")
print("=" * 50)
print("\nЭто зашифрованный maFile Steam.")
print("Для расшифровки нужен пароль шифрования.")
print("\nКак получить пароль:")
print("1. Это пароль, который вы устанавливали при создании maFile")
print("2. Обычно это ваш мастер-пароль Steam или специальный пароль")
print("3. Если вы не знаете пароль, создайте новый maFile")

password = input("\nВведите пароль шифрования: ")

if password:
    result = decrypt_mafile(encrypted_mafile, password)
    
    if result:
        print("\n" + "=" * 50)
        print("SUCCESS: maFile успешно расшифрован!")
        print("\nСодержимое maFile:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        
        if 'shared_secret' in result:
            print("\n" + "=" * 50)
            print("ВАШ SHARED_SECRET:")
            print(result['shared_secret'])
            print("\nТеперь добавьте его в configs/shared_secrets.cfg")
        else:
            print("\nВНИМАНИЕ: shared_secret не найден в maFile")
    else:
        print("\nERROR: Не удалось расшифровать maFile")
        print("Возможно неверный пароль шифрования")
else:
    print("Пароль не введен. Попробуйте снова.")