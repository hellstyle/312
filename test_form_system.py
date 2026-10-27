#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тестовый скрипт для проверки системы обработки форм.
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

from form_processor import FormProcessor


def test_form_processor():
    """Тестирует систему обработки форм."""
    print("TEST: Testing Form Processing System")
    print("=" * 50)
    
    processor = FormProcessor()
    
    # Тест 1: Парсинг правильной формы
    print("\n[TEST 1] Parse correct form")
    form_message = """
    Сервер: Emerald
    Уровни: с 1 по 10
    Согласие с рисками: ДА
    Согласие с правилами FunPay: ДА
    """
    
    form_data = processor.parse_form(form_message)
    print(f"Form data: {form_data}")
    assert form_data is not None, "Should parse correct form"
    assert form_data['server'] == 'Emerald', "Server should be Emerald"
    assert form_data['level_from'] == '1', "Level from should be 1"
    assert form_data['level_to'] == '10', "Level to should be 10"
    print("[OK] Test 1 passed")
    
    # Тест 2: Валидация правильной формы
    print("\n[TEST 2] Validate correct form")
    is_valid, error_message = processor.validate_form(form_data)
    print(f"Valid: {is_valid}, Error: {error_message}")
    assert is_valid, "Correct form should be valid"
    print("[OK] Test 2 passed")
    
    # Тест 3: Валидация формы с неправильным согласием
    print("\n[TEST 3] Validate form with wrong consent")
    form_data_wrong = form_data.copy()
    form_data_wrong['risk_consent'] = 'НЕТ'
    is_valid, error_message = processor.validate_form(form_data_wrong)
    print(f"Valid: {is_valid}, Error: {error_message}")
    assert not is_valid, "Should be invalid without consent"
    print("[OK] Test 3 passed")
    
    # Тест 4: Валидация формы с неправильными уровнями
    print("\n[TEST 4] Validate form with wrong levels")
    form_data_wrong_levels = form_data.copy()
    form_data_wrong_levels['level_from'] = '10'
    form_data_wrong_levels['level_to'] = '5'
    is_valid, error_message = processor.validate_form(form_data_wrong_levels)
    print(f"Valid: {is_valid}, Error: {error_message}")
    assert not is_valid, "Should be invalid with wrong levels"
    print("[OK] Test 4 passed")
    
    # Тест 5: Сохранение и проверка формы
    print("\n[TEST 5] Submit and check form")
    processor.submit_form(12345, "test_user", form_data)
    has_form, checked_data = processor.check_form_submitted(12345, "test_user")
    print(f"Has form: {has_form}")
    assert has_form, "Should have submitted form"
    assert checked_data['server'] == 'Emerald', "Should retrieve correct data"
    print("[OK] Test 5 passed")
    
    # Тест 6: Проверка несуществующей формы
    print("\n[TEST 6] Check non-existent form")
    has_form, checked_data = processor.check_form_submitted(99999, "nonexistent_user")
    print(f"Has form: {has_form}")
    assert not has_form, "Should not have form for non-existent user"
    print("[OK] Test 6 passed")
    
    # Тест 7: Генерация summary формы
    print("\n[TEST 7] Generate form summary")
    summary = processor.get_form_summary(form_data)
    print(f"Summary:\n{summary}")
    assert 'Emerald' in summary, "Summary should contain server name"
    assert '10' in summary, "Summary should contain level count"
    print("[OK] Test 7 passed")
    
    # Тест 8: Сброс формы
    print("\n[TEST 8] Reset form")
    processor.reset_form(12345, "test_user")
    has_form, checked_data = processor.check_form_submitted(12345, "test_user")
    print(f"Has form after reset: {has_form}")
    assert not has_form, "Should not have form after reset"
    print("[OK] Test 8 passed")
    
    # Тест 9: Парсинг формы в другом формате
    print("\n[TEST 9] Parse form in different format")
    form_message_alt = "Server: Diamond, Levels: from 5 to 15, Risk consent: YES, FunPay consent: YES"
    form_data_alt = processor.parse_form(form_message_alt)
    print(f"Form data: {form_data_alt}")
    assert form_data_alt is not None, "Should parse alternative format"
    print("[OK] Test 9 passed")
    
    print("\n" + "=" * 50)
    print("[SUCCESS] All tests passed!")
    print("\n[STATS] Form processor is ready for use")


if __name__ == "__main__":
    try:
        test_form_processor()
    except Exception as e:
        print(f"[ERROR] Testing error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
