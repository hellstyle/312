#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль для обработки форм заказов и проверки их заполнения.
"""

import re
import logging
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger("FormProcessor")


class FormProcessor:
    """Класс для обработки и валидации форм заказов."""
    
    def __init__(self):
        self.submitted_forms: Dict[int, dict] = {}  # {chat_id: {form_data, timestamp}}
        self.form_expiry = 3600  # 1 час для действия формы
    
    def parse_form(self, message: str) -> Optional[Dict[str, str]]:
        """
        Парсит сообщение с формой заказа.
        
        :param message: текст сообщения с формой
        :return: словарь с данными формы или None при ошибке
        """
        form_data = {}
        
        # Паттерны для поиска полей формы
        patterns = {
            'server': [r'сервер:\s*([^\n,]+)', r'server:\s*([^\n,]+)'],
            'levels': [r'уровни:\s*с\s*(\d+)\s*по\s*(\d+)', r'levels:\s*from\s*(\d+)\s*to\s*(\d+)'],
            'risk_consent': [r'согласие с рисками:\s*(ДА|НЕТ|YES|NO)', r'risk consent:\s*(ДА|НЕТ|YES|NO)'],
            'funpay_consent': [r'согласие с правилами funpay:\s*(ДА|НЕТ|YES|NO)', 
                              r'funpay consent:\s*(ДА|НЕТ|YES|NO)']
        }
        
        # Парсинг полей
        for field, field_patterns in patterns.items():
            for pattern in field_patterns:
                match = re.search(pattern, message, re.IGNORECASE)
                if match:
                    if field == 'levels':
                        form_data['level_from'] = match.group(1)
                        form_data['level_to'] = match.group(2)
                    else:
                        form_data[field] = match.group(1).strip()
                    break
        
        # Проверяем, что все обязательные поля заполнены
        required_fields = ['server', 'level_from', 'level_to', 'risk_consent', 'funpay_consent']
        for field in required_fields:
            if field not in form_data:
                logger.warning(f"Поле {field} не найдено в форме")
                return None
        
        return form_data
    
    def validate_form(self, form_data: Dict[str, str]) -> Tuple[bool, str]:
        """
        Валидирует данные формы.
        
        :param form_data: данные формы
        :return: (is_valid, error_message)
        """
        # Проверка согласия с рисками
        if form_data['risk_consent'].upper() not in ['ДА', 'YES']:
            return False, "❌ Необходимо согласие с возможностью наказания (ДА)"
        
        # Проверка согласия с правилами FunPay
        if form_data['funpay_consent'].upper() not in ['ДА', 'YES']:
            return False, "❌ Необходимо согласие с правилами FunPay (ДА)"
        
        # Проверка уровней
        try:
            level_from = int(form_data['level_from'])
            level_to = int(form_data['level_to'])
            
            if level_from >= level_to:
                return False, "❌ Начальный уровень должен быть меньше конечного"
            
            if level_to - level_from > 100:
                return False, "❌ Слишком большой диапазон уровней (максимум 100)"
            
        except ValueError:
            return False, "❌ Некорректные значения уровней"
        
        # Проверка названия сервера
        if not form_data['server'] or len(form_data['server']) < 2:
            return False, "❌ Некорректное название сервера"
        
        return True, ""
    
    def submit_form(self, chat_id: int, username: str, form_data: Dict[str, str],
                    lot_name: str = "") -> bool:
        """
        Сохраняет заполненную форму.

        :param chat_id: ID чата
        :param username: имя пользователя
        :param form_data: данные формы
        :param lot_name: название лота/услуги (из контекста заказа)
        :return: True если успешно
        """
        self.submitted_forms[chat_id] = {
            'username': username,
            'form_data': form_data,
            'lot_name': lot_name,
            'timestamp': datetime.now()
        }
        logger.info(f"Форма сохранена для {username} (chat_id: {chat_id}, лот: {lot_name!r})")
        return True
    
    def check_form_submitted(self, chat_id: int, username: str) -> Tuple[bool, Optional[Dict], str]:
        """
        Проверяет, отправлял ли пользователь форму.

        :param chat_id: ID чата
        :param username: имя пользователя
        :return: (is_submitted, form_data, lot_name)
        """
        if chat_id not in self.submitted_forms:
            return False, None, ""

        form_record = self.submitted_forms[chat_id]

        # Проверяем, не истекло ли время действия формы
        if datetime.now() - form_record['timestamp'] > timedelta(seconds=self.form_expiry):
            del self.submitted_forms[chat_id]
            logger.info(f"Форма истекла для {username}")
            return False, None, ""

        # Проверяем, что форма принадлежит тому же пользователю
        if form_record['username'] != username:
            logger.warning(f"Несоответствие пользователя для chat_id {chat_id}")
            return False, None, ""

        return True, form_record['form_data'], form_record.get('lot_name', ''), form_record.get('lot_name', '')
    
    def get_form_summary(self, form_data: Dict[str, str], lot_name: str = "") -> str:
        """
        Генерирует краткое описание формы.

        :param form_data: данные формы
        :param lot_name: название лота (необязательно)
        :return: текстовое описание
        """
        levels_count = int(form_data['level_to']) - int(form_data['level_from'])
        lot_line = f"\n🎮 Услуга: {lot_name}" if lot_name else ""
        summary = (
            f"📋 ДАННЫЕ ФОРМЫ:{lot_line}\n"
            f"🔸 Сервер: {form_data['server']}\n"
            f"🔸 Уровни: с {form_data['level_from']} по {form_data['level_to']} "
            f"(всего {levels_count} ед.)\n"
            f"🔸 Согласие с рисками: {form_data['risk_consent']}\n"
            f"🔸 Согласие с правилами FunPay: {form_data['funpay_consent']}\n\n"
            f"⚠️ Навыки дальнобоя — 1 единица уровня!"
        )
        return summary
    
    def cleanup_expired_forms(self):
        """Очищает истекшие формы."""
        now = datetime.now()
        expired_chats = []
        
        for chat_id, form_record in self.submitted_forms.items():
            if now - form_record['timestamp'] > timedelta(seconds=self.form_expiry):
                expired_chats.append(chat_id)
        
        for chat_id in expired_chats:
            del self.submitted_forms[chat_id]
            logger.debug(f"Удалена истекшая форма для chat_id {chat_id}")
    
    def reset_form(self, chat_id: int, username: str):
        """
        Сбрасывает форму для пользователя (например, для повторного заполнения).
        
        :param chat_id: ID чата
        :param username: имя пользователя
        """
        if chat_id in self.submitted_forms:
            del self.submitted_forms[chat_id]
            logger.info(f"Форма сброшена для {username}")


# Глобальный экземпляр
_form_processor: FormProcessor = None


def init_form_processor() -> FormProcessor:
    """Инициализация глобального процессора форм."""
    global _form_processor
    _form_processor = FormProcessor()
    return _form_processor


def get_form_processor() -> FormProcessor:
    """Получение глобального процессора форм."""
    return _form_processor