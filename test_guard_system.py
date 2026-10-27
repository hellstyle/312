#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тестовый скрипт для проверки системы Guard кодов с ограничениями попыток.
"""

import sys
import os
from datetime import datetime, timedelta

# Устанавливаем кодировку UTF-8 для Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(__file__))

from purchase_checker import PurchaseChecker


def test_purchase_checker():
    """Тестирует систему проверки покупок и ограничений попыток."""
    print("TEST: Testing Guard code system")
    print("=" * 50)
    
    # Создаем мок-объект кардинала
    class MockCardinal:
        pass
    
    cardinal = MockCardinal()
    checker = PurchaseChecker(cardinal)
    
    # Тест 1: Проверка без покупки
    print("\n[TEST 1] Check without purchase")
    can_request, message = checker.can_request_code(12345, "test_user")
    print(f"Result: {can_request}")
    print(f"Message: {message}")
    assert not can_request, "Should be denied without purchase"
    print("[OK] Test 1 passed")
    
    # Тест 2: Регистрация покупки
    print("\n[TEST 2] Register purchase")
    checker.add_purchase(12345, "test_user")
    print("Purchase registered for chat_id=12345")
    
    # Тест 3: Проверка с покупкой
    print("\n[TEST 3] Check with purchase")
    can_request, message = checker.can_request_code(12345, "test_user")
    print(f"Result: {can_request}")
    print(f"Message: {message}")
    assert can_request, "Should be allowed with purchase"
    print("[OK] Test 3 passed")
    
    # Тест 4: Проверка попыток
    print("\n[TEST 4] Check attempt system")
    for i in range(1, 4):
        remaining = checker.record_code_attempt(12345, "test_user")
        print(f"Attempt {i}: remaining attempts = {remaining}")
    
    # Тест 5: Проверка превышения лимита
    print("\n[TEST 5] Check attempt limit exceeded")
    can_request, message = checker.can_request_code(12345, "test_user")
    print(f"Result: {can_request}")
    print(f"Message: {message}")
    assert not can_request, "Should be denied after 3 attempts"
    print("[OK] Test 5 passed")
    
    # Тест 6: Сброс попыток
    print("\n[TEST 6] Reset attempts")
    checker.reset_attempts(12345, "test_user")
    print("Attempts reset")
    
    # Тест 7: Проверка после сброса
    print("\n[TEST 7] Check after reset")
    can_request, message = checker.can_request_code(12345, "test_user")
    print(f"Result: {can_request}")
    print(f"Message: {message}")
    assert can_request, "Should be allowed after reset"
    print("[OK] Test 7 passed")
    
    # Тест 8: Проверка истечения времени покупки
    print("\n[TEST 8] Check purchase expiry")
    # Очищаем предыдущие записи и добавляем устаревшую
    checker.purchased_users.clear()
    old_time = datetime.now() - timedelta(seconds=4000)  # Больше часа
    checker.purchased_users.add((12345, old_time))
    
    can_request, message = checker.can_request_code(12345, "test_user")
    print(f"Result: {can_request}")
    print(f"Message: {message}")
    assert not can_request, "Should be denied when purchase expired"
    print("[OK] Test 8 passed")
    
    print("\n" + "=" * 50)
    print("[SUCCESS] All tests passed!")
    print("\n[STATS] System statistics:")
    print(f"• Max attempts: {checker.max_attempts}")
    print(f"• Verification window: {checker.verification_window} seconds")
    print(f"• Registered purchases: {len(checker.purchased_users)}")
    print(f"• Active attempts: {len(checker.code_attempts)}")


if __name__ == "__main__":
    try:
        test_purchase_checker()
    except Exception as e:
        print(f"[ERROR] Testing error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
