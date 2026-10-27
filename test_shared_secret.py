#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тестовый скрипт для проверки системы shared_secret Guard.
"""

import sys
import os

# Устанавливаем кодировку UTF-8 для Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(__file__))

from shared_secret_guard import SharedSecretGuard, SharedSecretManager


def test_shared_secret_guard():
    """Тестирует систему shared_secret Guard."""
    print("TEST: Testing Shared Secret Guard System")
    print("=" * 50)
    
    # Тестовый shared_secret (это пример, для реального использования нужен настоящий secret)
    # Это демо secret для тестирования
    test_secret = "wJ4RACRjPJXFPEyPWjHQOx+2T28Sj+HyMA4CmC6Y6yM="
    
    # Тест 1: Создание Guard с shared_secret
    print("\n[TEST 1] Create Guard with shared_secret")
    guard = SharedSecretGuard(test_secret)
    print(f"Guard enabled: {guard.enabled}")
    assert guard.enabled, "Guard should be enabled with valid secret"
    print("[OK] Test 1 passed")
    
    # Тест 2: Генерация кода
    print("\n[TEST 2] Generate code")
    code = guard.generate_code()
    print(f"Generated code: {code}")
    assert code is not None, "Should generate code"
    assert len(code) == 6, "Code should be 6 characters"
    assert code.isdigit(), "Code should be numeric"
    print("[OK] Test 2 passed")
    
    # Тест 3: Генерация кода с временем
    print("\n[TEST 3] Generate code with timestamp")
    import time
    current_time = int(time.time())
    code_with_time = guard.generate_code(current_time)
    print(f"Code with timestamp: {code_with_time}")
    assert code_with_time is not None, "Should generate code with timestamp"
    print("[OK] Test 3 passed")
    
    # Тест 4: Получение кода с информацией о времени
    print("\n[TEST 4] Get code with time remaining")
    code, remaining = guard.get_code_with_time_remaining()
    print(f"Code: {code}, Remaining: {remaining} seconds")
    assert code is not None, "Should get code"
    assert 0 <= remaining <= 30, "Remaining time should be 0-30 seconds"
    print("[OK] Test 4 passed")
    
    # Тест 5: Проверка валидности кода
    print("\n[TEST 5] Validate code")
    is_valid = guard.validate_code(code)
    print(f"Code validation: {is_valid}")
    assert is_valid, "Generated code should be valid"
    print("[OK] Test 5 passed")
    
    # Тест 6: Тест менеджера
    print("\n[TEST 6] Test SharedSecretManager")
    manager = SharedSecretManager()
    success = manager.add_account("test_user", test_secret)
    print(f"Account added: {success}")
    assert success, "Should add account successfully"
    print("[OK] Test 6 passed")
    
    # Тест 7: Получение кода через менеджер
    print("\n[TEST 7] Get code through manager")
    manager_code = manager.get_code("test_user")
    print(f"Manager code: {manager_code}")
    assert manager_code is not None, "Should get code through manager"
    assert len(manager_code) == 6, "Manager code should be 6 characters"
    print("[OK] Test 7 passed")
    
    # Тест 8: Получение кода с информацией через менеджер
    print("\n[TEST 8] Get code with info through manager")
    code_info = manager.get_code_with_info("test_user")
    print(f"Code info: {code_info}")
    assert code_info is not None, "Should get code info"
    assert code_info['valid'], "Code should be valid"
    assert 'remaining_seconds' in code_info, "Should have remaining time"
    print("[OK] Test 8 passed")
    
    # Тест 9: Список аккаунтов
    print("\n[TEST 9] List accounts")
    accounts = manager.list_accounts()
    print(f"Accounts: {accounts}")
    assert "test_user" in accounts, "Should contain test_user"
    print("[OK] Test 9 passed")
    
    # Тест 10: Удаление аккаунта
    print("\n[TEST 10] Remove account")
    removed = manager.remove_account("test_user")
    print(f"Account removed: {removed}")
    assert removed, "Should remove account successfully"
    accounts_after = manager.list_accounts()
    assert "test_user" not in accounts_after, "Should not contain test_user after removal"
    print("[OK] Test 10 passed")
    
    print("\n" + "=" * 50)
    print("[SUCCESS] All tests passed!")
    print("\n[INFO] Shared Secret Guard system is ready for use")
    print("\n[NEXT STEPS]")
    print("1. Получите real shared_secret из maFiles")
    print("2. Добавьте аккаунты через SharedSecretManager")
    print("3. Интегрируйте с GuardHandler")
    print("4. Тестируйте с реальными данными")


if __name__ == "__main__":
    try:
        test_shared_secret_guard()
    except Exception as e:
        print(f"[ERROR] Testing error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
