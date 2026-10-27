"""
Модуль для интеграции с Gmail для получения Steam Guard кодов.

Логика: Steam письма НЕ содержат username покупателя — только код подтверждения.
Поэтому хранится последний полученный код и выдаётся любому авторизованному покупателю.
"""

import imaplib
import email
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Optional
import logging

logger = logging.getLogger("GmailGuard")

# TTL кода — 30 секунд (Steam Guard меняет коды каждые 30 сек)
CODE_TTL_SECONDS = 30


class GmailGuardIntegration:
    """Класс для работы с Gmail и получения Steam Guard кодов."""

    def __init__(self, email_address: str, app_password: str):
        self.email_address = email_address
        self.app_password = app_password
        self.imap_server = "imap.gmail.com"
        self.imap_port = 993

        self.connected = False
        self.mail: Optional[imaplib.IMAP4_SSL] = None

        # Последний полученный код (без привязки к username)
        self._last_code: Optional[str] = None
        self._last_code_time: Optional[datetime] = None
        # ID последнего обработанного письма (чтобы не парсить одно и то же)
        self._last_email_id: Optional[bytes] = None

        self.lock = threading.Lock()
        self.running = False
        self.check_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Подключение
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Подключение к Gmail по IMAP SSL."""
        try:
            self.mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            self.mail.login(self.email_address, self.app_password)
            self.connected = True
            logger.info(f"Gmail Guard: подключен как {self.email_address}")
            return True
        except Exception as e:
            logger.error(f"Gmail Guard: ошибка подключения — {e}")
            self.connected = False
            return False

    def disconnect(self):
        """Отключение от Gmail."""
        if self.connected and self.mail:
            try:
                self.mail.close()
                self.mail.logout()
            except Exception:
                pass
            self.connected = False
            logger.info("Gmail Guard: отключён")

    def _ensure_connected(self) -> bool:
        """Переподключается если соединение потеряно."""
        if self.connected:
            try:
                self.mail.noop()
                return True
            except Exception:
                self.connected = False
        return self.connect()

    # ------------------------------------------------------------------
    # Получение кода
    # ------------------------------------------------------------------

    def get_guard_code(self, _username: str = "") -> Optional[str]:
        """
        Возвращает последний актуальный Steam Guard код.
        username не используется — код выдаётся любому авторизованному покупателю.

        :return: код (5-6 символов) или None если нет актуального кода
        """
        with self.lock:
            if self._last_code and self._last_code_time:
                age = (datetime.now() - self._last_code_time).total_seconds()
                if age <= CODE_TTL_SECONDS:
                    remaining = int(CODE_TTL_SECONDS - age)
                    logger.info(f"Gmail Guard: выдаю код {self._last_code} (истекает через {remaining} сек)")
                    return self._last_code
                else:
                    logger.debug("Gmail Guard: последний код устарел")
                    self._last_code = None
            return None

    # ------------------------------------------------------------------
    # Парсинг писем
    # ------------------------------------------------------------------

    def fetch_latest_guard_code(self):
        """Забирает из INBOX последнее письмо от Steam и извлекает Guard код."""
        if not self._ensure_connected():
            return

        try:
            self.mail.select("INBOX")

            # Ищем письма от Steam за последние 2 минуты (с запасом)
            since_date = (datetime.now() - timedelta(minutes=2)).strftime("%d-%b-%Y")
            status, messages = self.mail.search(
                None, f'(FROM "noreply@steampowered.com" SINCE {since_date})'
            )
            if status != "OK" or not messages[0]:
                return

            email_ids = messages[0].split()
            latest_id = email_ids[-1]  # только последнее письмо

            # Не обрабатываем одно и то же письмо дважды
            if latest_id == self._last_email_id:
                return

            status, msg_data = self.mail.fetch(latest_id, "(RFC822)")
            if status != "OK":
                return

            for response_part in msg_data:
                if not isinstance(response_part, tuple):
                    continue
                msg = email.message_from_bytes(response_part[1])
                body = self._get_body(msg)
                subject = msg.get("subject", "")

                code = self._extract_code(body) or self._extract_code(subject)
                if code:
                    with self.lock:
                        self._last_code = code
                        self._last_code_time = datetime.now()
                        self._last_email_id = latest_id
                    logger.info(f"Gmail Guard: получен новый код {code}")
                    return

        except Exception as e:
            logger.error(f"Gmail Guard: ошибка при чтении писем — {e}")
            self.connected = False  # переподключимся на следующей итерации

    def _get_body(self, msg) -> str:
        """Извлекает текстовое тело письма."""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        return part.get_payload(decode=True).decode("utf-8", errors="ignore")
                    except Exception:
                        pass
        else:
            try:
                return msg.get_payload(decode=True).decode("utf-8", errors="ignore")
            except Exception:
                pass
        return ""

    def _extract_code(self, text: str) -> Optional[str]:
        """
        Извлекает Steam Guard код из текста.
        Steam коды — ровно 5 символов из букв и цифр (A-Z, 0-9), без O и I.
        Ищем паттерны вида: 'код: ABCDE', 'code: ABCDE', или отдельно стоящий 5-символьный токен.
        """
        # Явные паттерны из Steam писем (RU и EN)
        explicit_patterns = [
            r'(?:Guard code|code|код)[:\s]+([A-Z0-9]{5})\b',
            r'\b([A-Z0-9]{5})\b',  # fallback — любой 5-символьный токен
        ]
        for pattern in explicit_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                candidate = match.group(1).upper()
                # Исключаем слова-мусор: даты, общие слова
                if not re.match(r'^(STEAM|EMAIL|GUARD|LOGIN|HTTPS|CLICK)$', candidate):
                    return candidate
        return None

    # ------------------------------------------------------------------
    # Фоновый цикл проверки
    # ------------------------------------------------------------------

    def start_auto_check(self, interval: int = 15):
        """Запускает фоновый поток проверки Gmail каждые `interval` секунд."""
        if self.running:
            return
        self.running = True

        def loop():
            while self.running:
                try:
                    self.fetch_latest_guard_code()
                except Exception as e:
                    logger.error(f"Gmail Guard: ошибка в фоновом цикле — {e}")
                time.sleep(interval)

        self.check_thread = threading.Thread(target=loop, daemon=True, name="GmailGuardLoop")
        self.check_thread.start()
        logger.info(f"Gmail Guard: фоновая проверка запущена (каждые {interval} сек)")

    def stop_auto_check(self):
        """Останавливает фоновый поток."""
        self.running = False
        if self.check_thread:
            self.check_thread.join(timeout=5)
        logger.info("Gmail Guard: фоновая проверка остановлена")
