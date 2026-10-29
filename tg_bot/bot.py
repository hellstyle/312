"""
В данном модуле написан Telegram бот.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from FunPayAPI import Account
from tg_bot.utils import NotificationTypes

if TYPE_CHECKING:
    from cardinal import Cardinal

import os
import sys
import time
import random
import string
import psutil
import telebot
from telebot.apihelper import ApiTelegramException
import logging

from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B, Message, CallbackQuery, BotCommand, \
    InputFile
from tg_bot import utils, static_keyboards as skb, keyboards as kb, CBT
from Utils import cardinal_tools, updater
from locales.localizer import Localizer

logger = logging.getLogger("TGBot")
localizer = Localizer()
_ = localizer.translate
telebot.apihelper.ENABLE_MIDDLEWARE = True


class TGBot:
    def __init__(self, cardinal: Cardinal):
        self.cardinal = cardinal
        if cardinal.MAIN_CFG["Telegram"]["proxy"]:
            telebot.apihelper.proxy = {"https": cardinal.MAIN_CFG["Telegram"]["proxy"],
                                       "http": cardinal.MAIN_CFG["Telegram"]["proxy"]}
        self.bot = telebot.TeleBot(self.cardinal.MAIN_CFG["Telegram"]["token"], parse_mode="HTML",
                                   allow_sending_without_reply=True, num_threads=5)

        self.file_handlers = {}  # хэндлеры, привязанные к получению файла.
        self.attempts = {}  # {user_id: attempts} - попытки авторизации в Telegram ПУ.
        self.init_messages = []  # [(chat_id, message_id)] - список сообщений о запуске TG бота.
        self.owner_chat_id = cardinal.MAIN_CFG["Telegram"].get("owner_chat_id", None)  # ID чата владельца для уведомлений

        # {
        #     chat_id: {
        #         user_id: {
        #             "state": "state",
        #             "data": { ... },
        #             "mid": int
        #         }
        #     }
        # }
        self.user_states = {}

        # {
        #    chat_id: {
        #        utils.NotificationTypes.new_message: bool,
        #        utils.NotificationTypes.new_order: bool,
        #        ...
        #    },
        # }
        #
        self.notification_settings = utils.load_notification_settings()  # настройки уведомлений.
        self.answer_templates = utils.load_answer_templates()  # заготовки ответов.
        self.authorized_users = utils.load_authorized_users()  # авторизированные пользователи.

        self.commands = {
            "menu": "cmd_menu",
            "profile": "cmd_profile",
            "restart": "cmd_restart",
            "check_updates": "cmd_check_updates",
            "update": "cmd_update",
            "golden_key": "cmd_golden_key",
            "ban": "cmd_ban",
            "unban": "cmd_unban",
            "black_list": "cmd_black_list",
            "upload_chat_img": "cmd_upload_chat_img",
            "upload_offer_img": "cmd_upload_offer_img",
            "upload_plugin": "cmd_upload_plugin",
            "test_lot": "cmd_test_lot",
            "logs": "cmd_logs",
            "about": "cmd_about",
            "sys": "cmd_sys",
            "get_backup": "cmd_get_backup",
            "create_backup": "cmd_create_backup",
            "upload_backup": "cmd_upload_backup",
            "del_logs": "cmd_del_logs",
            "power_off": "cmd_power_off",
            "watermark": "cmd_watermark",
            "code": "cmd_code",
            "scan_library": "cmd_scan_library",
            "add_game": "cmd_add_game",
        }
        self.__default_notification_settings = {
            utils.NotificationTypes.ad: 1,
            utils.NotificationTypes.announcement: 1
        }

    # User states
    def get_state(self, chat_id: int, user_id: int) -> dict | None:
        """
        Получает текущее состояние пользователя.

        :param chat_id: id чата.
        :param user_id: id пользователя.

        :return: данные состояния пользователя.
        """
        try:
            return self.user_states[chat_id][user_id]
        except KeyError:
            return None

    def set_state(self, chat_id: int, message_id: int, user_id: int, state: str, data: dict | None = None):
        """
        Устанавливает состояние для пользователя.

        :param chat_id: id чата.
        :param message_id: id сообщения, после которого устанавливается данное состояние.
        :param user_id: id пользователя.
        :param state: состояние.
        :param data: доп. данные.
        """
        if chat_id not in self.user_states:
            self.user_states[chat_id] = {}
        self.user_states[chat_id][user_id] = {"state": state, "mid": message_id, "data": data or {}}

    def clear_state(self, chat_id: int, user_id: int, del_msg: bool = False) -> int | None:
        """
        Очищает состояние пользователя.

        :param chat_id: id чата.
        :param user_id: id пользователя.
        :param del_msg: удалять ли сообщение, после которого было обозначено текущее состояние.

        :return: ID сообщения-инициатора или None, если состояние и так было пустое.
        """
        try:
            state = self.user_states[chat_id][user_id]
        except KeyError:
            return None

        msg_id = state.get("mid")
        del self.user_states[chat_id][user_id]
        if del_msg:
            try:
                self.bot.delete_message(chat_id, msg_id)
            except:
                pass
        return msg_id

    def check_state(self, chat_id: int, user_id: int, state: str) -> bool:
        """
        Проверяет, является ли состояние указанным.

        :param chat_id: id чата.
        :param user_id: id пользователя.
        :param state: состояние.

        :return: True / False
        """
        try:
            return self.user_states[chat_id][user_id]["state"] == state
        except KeyError:
            return False

    # Notification settings
    def is_notification_enabled(self, chat_id: int | str, notification_type: str) -> bool:
        """
        Включен ли указанный тип уведомлений в указанном чате?

        :param chat_id: ID Telegram чата.
        :param notification_type: тип уведомлений.
        """
        try:
            return bool(self.notification_settings[str(chat_id)][notification_type])
        except KeyError:
            return False

    def toggle_notification(self, chat_id: int, notification_type: str) -> bool:
        """
        Переключает указанный тип уведомлений в указанном чате и сохраняет настройки уведомлений.

        :param chat_id: ID Telegram чата.
        :param notification_type: тип уведомлений.

        :return: вкл / выкл указанный тип уведомлений в указанном чате.
        """
        chat_id = str(chat_id)
        if chat_id not in self.notification_settings:
            self.notification_settings[chat_id] = {}

        self.notification_settings[chat_id][notification_type] = not self.is_notification_enabled(chat_id,
                                                                                                  notification_type)
        utils.save_notification_settings(self.notification_settings)
        return self.notification_settings[chat_id][notification_type]

    # handler binders
    def is_file_handler(self, m: Message):
        return self.get_state(m.chat.id, m.from_user.id) and m.content_type in ["photo", "document"]

    def file_handler(self, state, handler):
        self.file_handlers[state] = handler

    def run_file_handlers(self, m: Message):
        if (state := self.get_state(m.chat.id, m.from_user.id)) is None \
                or state["state"] not in self.file_handlers:
            return
        try:
            self.file_handlers[state["state"]](m)
        except:
            logger.error(_("log_tg_handler_error"))
            logger.debug("TRACEBACK", exc_info=True)

    def msg_handler(self, handler, **kwargs):
        """
        Регистрирует хэндлер, срабатывающий при новом сообщении.

        :param handler: хэндлер.
        :param kwargs: аргументы для хэндлера.
        """
        bot_instance = self.bot

        @bot_instance.message_handler(**kwargs)
        def run_handler(message: Message):
            try:
                handler(message)
            except:
                logger.error(_("log_tg_handler_error"))
                logger.debug("TRACEBACK", exc_info=True)

    def cbq_handler(self, handler, func, **kwargs):
        """
        Регистрирует хэндлер, срабатывающий при новом callback'е.

        :param handler: хэндлер.
        :param func: функция-фильтр.
        :param kwargs: аргументы для хэндлера.
        """
        bot_instance = self.bot

        @bot_instance.callback_query_handler(func, **kwargs)
        def run_handler(call: CallbackQuery):
            try:
                handler(call)
            except:
                logger.error(_("log_tg_handler_error"))
                logger.debug("TRACEBACK", exc_info=True)

    def mdw_handler(self, handler, **kwargs):
        """
        Регистрирует промежуточный хэндлер.

        :param handler: хэндлер.
        :param kwargs: аргументы для хэндлера.
        """
        bot_instance = self.bot

        @bot_instance.middleware_handler(**kwargs)
        def run_handler(bot, update):
            try:
                handler(bot, update)
            except:
                logger.error(_("log_tg_handler_error"))
                logger.debug("TRACEBACK", exc_info=True)

    # Система свой-чужой 0_0
    def setup_chat_notifications(self, bot: TGBot, m: Message):
        """
        Устанавливает настройки уведомлений по умолчанию в новом чате.
        """
        if str(m.chat.id) in self.notification_settings and m.from_user.id in self.authorized_users and \
                self.is_notification_enabled(m.chat.id, NotificationTypes.critical):
            return
        elif str(m.chat.id) in self.notification_settings and m.from_user.id in self.authorized_users and not \
                self.is_notification_enabled(m.chat.id, NotificationTypes.critical):
            self.notification_settings[str(m.chat.id)][NotificationTypes.critical] = 1
            utils.save_notification_settings(self.notification_settings)
            return
        elif str(m.chat.id) not in self.notification_settings:
            self.notification_settings[str(m.chat.id)] = self.__default_notification_settings.copy()
            utils.save_notification_settings(self.notification_settings)

    def reg_admin(self, m: Message):
        """
        Проверяет, есть ли пользователь в списке пользователей с доступом к ПУ TG.
        """
        lang = m.from_user.language_code
        if m.chat.type != "private" or (self.attempts.get(m.from_user.id, 0) >= 5) or m.text is None:
            return
        if not self.cardinal.block_tg_login and \
                cardinal_tools.check_password(m.text, self.cardinal.MAIN_CFG["Telegram"]["secretKeyHash"]):
            self.send_notification(text=_("access_granted_notification", m.from_user.username, m.from_user.id),
                                   notification_type=NotificationTypes.critical, pin=True)
            self.authorized_users[m.from_user.id] = {}
            utils.save_authorized_users(self.authorized_users)
            if str(m.chat.id) not in self.notification_settings or not self.is_notification_enabled(m.chat.id,
                                                                                                    NotificationTypes.critical):
                self.notification_settings[str(m.chat.id)] = self.__default_notification_settings.copy()
                self.notification_settings[str(m.chat.id)][NotificationTypes.critical] = 1
                utils.save_notification_settings(self.notification_settings)
            text = _("access_granted", language=lang)
            kb_links = None
            logger.warning(_("log_access_granted", m.from_user.username, m.from_user.id))
        else:
            self.attempts[m.from_user.id] = self.attempts.get(m.from_user.id, 0) + 1
            text = _("access_denied", m.from_user.username, language=lang)
            kb_links = kb.links(language=lang)
            logger.warning(_("log_access_attempt", m.from_user.username, m.from_user.id))
        self.bot.send_message(m.chat.id, text, reply_markup=kb_links)

    def ignore_unauthorized_users(self, c: CallbackQuery):
        """
        Игнорирует callback'и от не авторизированных пользователей.
        """
        logger.warning(_("log_click_attempt", c.from_user.username, c.from_user.id, c.message.chat.username,
                         c.message.chat.id))
        self.attempts[c.from_user.id] = self.attempts.get(c.from_user.id, 0) + 1
        if self.attempts[c.from_user.id] <= 5:
            self.bot.answer_callback_query(c.id, _("adv_fpc", language=c.from_user.language_code), show_alert=True)
        return

    # Команды
    def send_settings_menu(self, m: Message):
        """
        Отправляет основное меню настроек (новым сообщением).
        """
        self.bot.send_message(m.chat.id, _("desc_main"), reply_markup=skb.SETTINGS_SECTIONS())

    def send_profile(self, m: Message):
        """
        Отправляет статистику аккаунта.
        """
        self.bot.send_message(m.chat.id, utils.generate_profile_text(self.cardinal),
                              reply_markup=skb.REFRESH_BTN())

    def act_change_cookie(self, m: Message):
        """
        Активирует режим ввода golden_key.
        """
        result = self.bot.send_message(m.chat.id, _("act_change_golden_key"), reply_markup=skb.CLEAR_STATE_BTN())
        self.set_state(m.chat.id, result.id, m.from_user.id, CBT.CHANGE_GOLDEN_KEY)

    def change_cookie(self, m: Message):
        """
        Меняет golden_key аккаунта FunPay.
        """
        self.clear_state(m.chat.id, m.from_user.id, True)
        golden_key = m.text
        if len(golden_key) != 32 or golden_key != golden_key.lower() or len(golden_key.split()) != 1:
            self.bot.send_message(m.chat.id, _("cookie_incorrect_format"))
            return
        self.bot.delete_message(m.chat.id, m.id)
        new_account = Account(golden_key, self.cardinal.account.user_agent, proxy=self.cardinal.proxy,
                              locale=self.cardinal.account.locale)
        try:
            new_account.get()
        except:
            logger.warning("Произошла ошибка")  # locale
            logger.debug("TRACEBACK", exc_info=True)
            self.bot.send_message(m.chat.id, _("cookie_error"))
            return

        one_acc = False
        if new_account.id == self.cardinal.account.id or self.cardinal.account.id is None:
            one_acc = True
            self.cardinal.account.golden_key = golden_key
            try:
                self.cardinal.account.get()
            except:
                logger.warning("Произошла ошибка")  # locale
                logger.debug("TRACEBACK", exc_info=True)
                self.bot.send_message(m.chat.id, _("cookie_error"))
                return
            accs = f" (<a href='https://funpay.com/users/{new_account.id}/'>{new_account.username}</a>)"
        else:
            accs = f" (<a href='https://funpay.com/users/{self.cardinal.account.id}/'>" \
                   f"{self.cardinal.account.username}</a> ➔ <a href='https://funpay.com/users/{new_account.id}/'>" \
                   f"{new_account.username}</a>)"

        self.cardinal.MAIN_CFG.set("FunPay", "golden_key", golden_key)
        self.cardinal.save_config(self.cardinal.MAIN_CFG, "configs/_main.cfg")
        self.bot.send_message(m.chat.id, f'{_("cookie_changed", accs)}{_("cookie_changed2") if not one_acc else ""}',
                              disable_web_page_preview=True)

    def update_profile(self, c: CallbackQuery):
        new_msg = self.bot.send_message(c.message.chat.id, _("updating_profile"))
        try:
            self.cardinal.account.get()
            self.cardinal.balance = self.cardinal.get_balance()
        except:
            self.bot.edit_message_text(_("profile_updating_error"), new_msg.chat.id, new_msg.id)
            logger.debug("TRACEBACK", exc_info=True)
            self.bot.answer_callback_query(c.id)
            return

        self.bot.delete_message(new_msg.chat.id, new_msg.id)
        self.bot.edit_message_text(utils.generate_profile_text(self.cardinal), c.message.chat.id,
                                   c.message.id, reply_markup=skb.REFRESH_BTN())

    def act_manual_delivery_test(self, m: Message):
        """
        Активирует режим ввода названия лота для ручной генерации ключа теста автовыдачи.
        """
        result = self.bot.send_message(m.chat.id, _("create_test_ad_key"), reply_markup=skb.CLEAR_STATE_BTN())
        self.set_state(m.chat.id, result.id, m.from_user.id, CBT.MANUAL_AD_TEST)

    def manual_delivery_text(self, m: Message):
        """
        Генерирует ключ теста автовыдачи (ручной режим).
        """
        self.clear_state(m.chat.id, m.from_user.id, True)
        lot_name = m.text.strip()
        key = "".join(random.sample(string.ascii_letters + string.digits, 50))
        self.cardinal.delivery_tests[key] = lot_name

        logger.info(_("log_new_ad_key", m.from_user.username, m.from_user.id, lot_name, key))
        self.bot.send_message(m.chat.id, _("test_ad_key_created", utils.escape(lot_name), key))

    def act_ban(self, m: Message):
        """
        Активирует режим ввода никнейма пользователя, которого нужно добавить в ЧС.
        """
        result = self.bot.send_message(m.chat.id, _("act_blacklist"), reply_markup=skb.CLEAR_STATE_BTN())
        self.set_state(m.chat.id, result.id, m.from_user.id, CBT.BAN)

    def ban(self, m: Message):
        """
        Добавляет пользователя в ЧС.
        """
        self.clear_state(m.chat.id, m.from_user.id, True)
        nickname = m.text.strip()

        if nickname in self.cardinal.blacklist:
            self.bot.send_message(m.chat.id, _("already_blacklisted", nickname))
            return

        self.cardinal.blacklist.append(nickname)
        cardinal_tools.cache_blacklist(self.cardinal.blacklist)
        logger.info(_("log_user_blacklisted", m.from_user.username, m.from_user.id, nickname))
        self.bot.send_message(m.chat.id, _("user_blacklisted", nickname))

    def act_unban(self, m: Message):
        """
        Активирует режим ввода никнейма пользователя, которого нужно удалить из ЧС.
        """
        result = self.bot.send_message(m.chat.id, _("act_unban"), reply_markup=skb.CLEAR_STATE_BTN())
        self.set_state(m.chat.id, result.id, m.from_user.id, CBT.UNBAN)

    def unban(self, m: Message):
        """
        Удаляет пользователя из ЧС.
        """
        self.clear_state(m.chat.id, m.from_user.id, True)
        nickname = m.text.strip()
        if nickname not in self.cardinal.blacklist:
            self.bot.send_message(m.chat.id, _("not_blacklisted", nickname))
            return
        self.cardinal.blacklist.remove(nickname)
        cardinal_tools.cache_blacklist(self.cardinal.blacklist)
        logger.info(_("log_user_unbanned", m.from_user.username, m.from_user.id, nickname))
        self.bot.send_message(m.chat.id, _("user_unbanned", nickname))

    def send_ban_list(self, m: Message):
        """
        Отправляет ЧС.
        """
        if not self.cardinal.blacklist:
            self.bot.send_message(m.chat.id, _("blacklist_empty"))
            return
        blacklist = ", ".join(f"<code>{i}</code>" for i in sorted(self.cardinal.blacklist, key=lambda x: x.lower()))
        self.bot.send_message(m.chat.id, blacklist)

    def act_edit_watermark(self, m: Message):
        """
        Активирует режим ввода вотемарки сообщений.
        """
        watermark = self.cardinal.MAIN_CFG["Other"]["watermark"]
        watermark = f"\n<code>{utils.escape(watermark)}</code>" if watermark else ""
        result = self.bot.send_message(m.chat.id, _("act_edit_watermark").format(watermark),
                                       reply_markup=skb.CLEAR_STATE_BTN())
        self.set_state(m.chat.id, result.id, m.from_user.id, CBT.EDIT_WATERMARK)

    def edit_watermark(self, m: Message):
        self.clear_state(m.chat.id, m.from_user.id, True)
        watermark = m.text if m.text != "-" else ""
        if re.fullmatch(r"\[[a-zA-Z]+]", watermark):
            self.bot.reply_to(m, _("watermark_error"))
            return

        preview = f"<a href=\"https://sfunpay.com/s/chat/zb/wl/zbwl4vwc8cc1wsftqnx5.jpg\">⁢</a>" if not \
            utils.has_brand_mark(watermark) else \
            f"<a href=\"https://sfunpay.com/s/chat/kd/8i/kd8isyquw660kcueck3g.jpg\">⁢</a>"
        self.cardinal.MAIN_CFG["Other"]["watermark"] = watermark
        self.cardinal.save_config(self.cardinal.MAIN_CFG, "configs/_main.cfg")
        if watermark:
            logger.info(_("log_watermark_changed", m.from_user.username, m.from_user.id, watermark))
            self.bot.reply_to(m, preview + _("watermark_changed", watermark))
        else:
            logger.info(_("log_watermark_deleted", m.from_user.username, m.from_user.id))
            self.bot.reply_to(m, preview + _("watermark_deleted"))

    def send_logs(self, m: Message):
        """
        Отправляет файл логов.
        """
        if not os.path.exists("logs/log.log"):
            self.bot.send_message(m.chat.id, _("logfile_not_found"))
        else:
            self.bot.send_message(m.chat.id, _("logfile_sending"))
            try:
                with open("logs/log.log", "r", encoding="utf-8") as f:
                    self.bot.send_document(m.chat.id, f,
                                           caption=f'{_("gs_old_msg_mode").replace("{} ", "") if self.cardinal.old_mode_enabled else ""}')
                    f.seek(0)
                    file_content = f.read()
                    if "TRACEBACK" in file_content:
                        file_content, right = file_content.rsplit("TRACEBACK", 1)
                        file_content = "\n[".join(file_content.rsplit("\n[", 2)[-2:])
                        right = right.split("\n[", 1)[0]  # locale
                        result = f"<b>Текст последней ошибки:</b>\n\n[{utils.escape(file_content)}TRACEBACK{utils.escape(right)}"
                        while result:
                            text, result = result[:4096], result[4096:]
                            self.bot.send_message(m.chat.id, text)
                            time.sleep(0.5)
                    else:
                        self.bot.send_message(m.chat.id, "<b>Ошибок в последнем лог-файле не обнаружено.</b>")  # locale
            except:
                logger.warning("Не удалось отправить лог-файл")
                logger.debug("TRACEBACK", exc_info=True)
                self.bot.send_message(m.chat.id, _("logfile_error"))

    def del_logs(self, m: Message):
        """
        Удаляет старые лог-файлы.
        """
        logger.info(
            f"[IMPORTANT] Удаляю логи по запросу пользователя $MAGENTA@{m.from_user.username} (id: {m.from_user.id})$RESET.")
        deleted = 0  # locale
        for file in os.listdir("logs"):
            if not file.endswith(".log"):
                try:
                    os.remove(f"logs/{file}")
                    deleted += 1
                except:
                    continue
        self.bot.send_message(m.chat.id, _("logfile_deleted").format(deleted))

    def about(self, m: Message):
        """
        Отправляет информацию о текущей версии бота.
        """
        self.bot.send_message(m.chat.id, _("about", self.cardinal.VERSION))

    def check_updates(self, m: Message):
        curr_tag = f"v{self.cardinal.VERSION}"
        releases = updater.get_new_releases(curr_tag)
        if isinstance(releases, int):
            errors = {
                1: ["update_no_tags", ()],
                2: ["update_lasted", (curr_tag,)],
                3: ["update_get_error", ()],
            }
            self.bot.send_message(m.chat.id, _(errors[releases][0], *errors[releases][1]))
            return
        for release in releases:
            self.bot.send_message(m.chat.id, _("update_available", release.name, release.description))
            time.sleep(1)
        self.bot.send_message(m.chat.id, _("update_update"))

    def get_backup(self, m: Message):
        logger.info(
            f"[IMPORTANT] Получаю бэкап по запросу пользователя $MAGENTA@{m.from_user.username} (id: {m.from_user.id})$RESET.")
        if os.path.exists("backup.zip"):  # locale
            with open(file_path := "backup.zip", 'rb') as file:
                modification_time = os.path.getmtime(file_path)
                formatted_time = time.strftime('%d.%m.%Y %H:%M:%S', time.localtime(modification_time))
                try:
                    self.bot.send_document(chat_id=m.chat.id, document=InputFile(file),
                                           caption=f'{_("update_backup")}\n\n{formatted_time}')
                except:
                    logger.warning("Не удалось отправить бэкап")
                    logger.debug("TRACEBACK", exc_info=True)
                    self.bot.send_message(m.chat.id, _("update_backup_send_error"))

        else:
            self.bot.send_message(m.chat.id, _("update_backup_not_found"))

    def create_backup(self, m: Message):
        if updater.create_backup():
            self.bot.send_message(m.chat.id, _("update_backup_error"))
            return False
        self.get_backup(m)
        return True

    def update(self, m: Message):
        curr_tag = f"v{self.cardinal.VERSION}"
        releases = updater.get_new_releases(curr_tag)
        if isinstance(releases, int):
            errors = {
                1: ["update_no_tags", ()],
                2: ["update_lasted", (curr_tag,)],
                3: ["update_get_error", ()],
            }
            self.bot.send_message(m.chat.id, _(errors[releases][0], *errors[releases][1]))
            return

        if not self.create_backup(m):
            return
        release = releases[-1]
        if updater.download_zip(release.sources_link) \
                or (release_folder := updater.extract_update_archive()) == 1:
            self.bot.send_message(m.chat.id, _("update_download_error"))
            return
        self.bot.send_message(m.chat.id, _("update_downloaded").format(release.name, str(len(releases) - 1)))

        if updater.install_release(release_folder):
            self.bot.send_message(m.chat.id, _("update_install_error"))
            return

        if getattr(sys, 'frozen', False):
            self.bot.send_message(m.chat.id, _("update_done_exe"))
        else:
            self.bot.send_message(m.chat.id, _("update_done"))

    def send_system_info(self, m: Message):
        """
        Отправляет информацию о нагрузке на систему.
        """
        current_time = int(time.time())
        uptime = current_time - self.cardinal.start_time

        ram = psutil.virtual_memory()
        cpu_usage = "\n".join(
            f"    CPU {i}:  <code>{l}%</code>" for i, l in enumerate(psutil.cpu_percent(percpu=True)))
        self.bot.send_message(m.chat.id, _("sys_info", cpu_usage, psutil.Process().cpu_percent(),
                                           ram.total // 1048576, ram.used // 1048576, ram.free // 1048576,
                                           psutil.Process().memory_info().rss // 1048576,
                                           cardinal_tools.time_to_str(uptime), m.chat.id))

    def restart_cardinal(self, m: Message):
        """
        Перезапускает кардинал.
        """
        self.bot.send_message(m.chat.id, _("restarting"))
        cardinal_tools.restart_program()

    def tg_guard_code(self, m: Message):
        """
        Выдаёт Steam Guard код через Telegram бота.
        """
        from guard_handler import get_guard_handler
        from purchase_checker import get_purchase_checker
        
        username = m.from_user.username or str(m.from_user.id)
        chat_id = m.chat.id
        user_id = m.from_user.id
        
        # Проверка на авторизованного пользователя (владельца)
        is_authorized = user_id in self.authorized_users
        
        # Проверка лимита попыток через purchase_checker (пропускаем для владельца)
        pc = get_purchase_checker()
        if pc and not is_authorized:
            can_request, deny_message = pc.can_request_code(None, username)
            if not can_request:
                self.bot.send_message(chat_id, deny_message)
                return
            pc.record_code_attempt(None, username)
            remaining = pc.get_remaining_attempts(None)
            attempts_line = f"\n📊 Осталось попыток: {'∞' if remaining == -1 else remaining}"
        else:
            attempts_line = ""
            if is_authorized:
                remaining = -1  # Бесконечные попытки для владельца
        
        # Получение Guard кода
        gh = get_guard_handler()
        code_generated = False
        
        if gh and gh.enabled:
            try:
                code = gh.get_guard_code(username)
                if code and not code.startswith("❌"):
                    response_text = (
                        f"🔑 Код Steam Guard: <code>{code}</code>\n"
                        f"⏱ Действителен ~30 секунд{attempts_line}"
                    )
                    code_generated = True
                else:
                    if gh.use_shared_secret:
                        response_text = (
                            "⏳ Код не может быть сгенерирован (shared_secret).\n"
                            "Убедитесь, что аккаунт добавлен в систему.\n"
                            "Попробуйте снова или обратитесь к продавцу."
                        )
                    else:
                        response_text = (
                            "⏳ Код ещё не пришёл на почту.\n"
                            "Попробуйте через 15–30 секунд — коды обновляются автоматически.\n"
                            "Если долго нет — напишите продавцу."
                        )
            except Exception as ex:
                logger.error(f"Ошибка получения Guard кода в TG: {ex}")
                response_text = (
                    "❌ Ошибка при получении кода Steam Guard. "
                    "Попробуйте через 30 секунд или обратитесь к продавцу."
                )
        else:
            response_text = (
                "❌ Система Steam Guard не настроена. "
                "Обратитесь к продавцу для получения кода вручную."
            )
        
        self.bot.send_message(chat_id, response_text)
        
        # Отправка уведомления владельцу
        if code_generated and self.owner_chat_id:
            try:
                owner_message = (
                    f"🔔 Уведомление о Guard коде (TG)\n\n"
                    f"👤 Пользователь: {username}\n"
                    f"🔑 Код: {code}\n"
                    f"⏱ Действителен ~30 секунд\n"
                    f"📊 Осталось попыток: {'∞' if remaining == -1 else remaining}\n"
                    f"🆔 Chat ID: {chat_id}"
                )
                self.bot.send_message(self.owner_chat_id, owner_message)
                logger.info(f"Уведомление о Guard коде отправлено владельцу (TG): {username} -> {code}")
            except Exception as ex:
                logger.error(f"Ошибка отправки уведомления владельцу: {ex}")

    def tg_refund(self, m: Message):
        """
        Команда /refund <order_id> — ручной возврат средств по ID заказа.

        Использование:
          /refund ABCD1234        — возврат по ID заказа
          /refund                 — показывает справку
        """
        if m.from_user.id not in self.authorized_users:
            self.bot.send_message(m.chat.id, "⛔ Нет доступа.")
            return

        parts = m.text.strip().split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            self.bot.send_message(
                m.chat.id,
                "↩️ <b>Команда /refund</b>\n\n"
                "Возврат средств покупателю по ID заказа.\n\n"
                "<b>Использование:</b>\n"
                "<code>/refund ABCD1234</code>\n\n"
                "ID заказа можно найти в уведомлении о заказе или на FunPay."
            )
            return

        order_id = parts[1].strip().lstrip("#")
        self.bot.send_message(m.chat.id, f"⏳ Оформляю возврат по заказу <code>{order_id}</code>...")

        try:
            from refund_handler import manual_refund
            success = manual_refund(self.cardinal, order_id)
            if success:
                self.bot.send_message(
                    m.chat.id,
                    f"✅ Возврат по заказу <code>{order_id}</code> успешно оформлен."
                )
            else:
                self.bot.send_message(
                    m.chat.id,
                    f"❌ Не удалось оформить возврат по заказу <code>{order_id}</code>.\n"
                    f"Проверьте ID заказа и статус заказа на FunPay."
                )
        except Exception as e:
            logger.error(f"tg_refund: ошибка: {e}")
            self.bot.send_message(
                m.chat.id,
                f"❌ Ошибка при возврате: <code>{e}</code>"
            )

    def scan_steam_library(self, m: Message):
        """
        Сканирует библиотеку Steam и показывает список игр для создания шаблонов.
        """
        from auto_lot_creator import get_auto_lot_creator        
        creator = get_auto_lot_creator()
        if not creator:
            self.bot.send_message(m.chat.id, "❌ AutoLotCreator не инициализирован. Проверьте конфигурацию.")
            return
        
        try:
            self.bot.send_message(m.chat.id, "🔍 Сканирую библиотеку Steam...")
            
            # Получаем существующие лоты/шаблоны
            existing_lots = creator.get_existing_lots()
            
            # Получаем список игр
            games = creator.get_steam_library()
            if not games:
                self.bot.send_message(m.chat.id, "❌ Нет игр для создания шаблонов.")
                return
            
            # Фильтруем новые игры
            new_games = []
            for game in games:
                game_name = game.get('name', '').lower()
                if game_name not in existing_lots:
                    new_games.append(game)
            
            if not new_games:
                self.bot.send_message(m.chat.id, "✅ Все игры уже есть в лотах!")
                return
            
            # Показываем список новых игр
            message = f"🎮 Найдено {len(new_games)} новых игр для создания шаблонов:\n\n"
            for i, game in enumerate(new_games, 1):
                game_name = game.get('name', 'Unknown')
                message += f"{i}. {game_name}\n"
            
            if creator.create_templates:
                message += f"\n📋 Будут созданы шаблоны в auto_delivery.cfg"
                message += f"\n💰 Тип: {creator.lot_type}"
                if creator.lot_type == "rental":
                    message += f" (аренда {creator.rental_hours}ч за {creator.rental_price}RUB)"
                else:
                    message += f" (оффлайн {creator.offline_price}RUB)"
            else:
                message += f"\n💰 Тип лота: {creator.lot_type}\n"
                if creator.lot_type == "rental":
                    message += f"⏱ Аренда: {creator.rental_hours} часов за {creator.rental_price} RUB\n"
                else:
                    message += f"💵 Цена: {creator.offline_price} RUB\n"
            
            message += f"\n🤖 Маркер автовыдачи: {'✅' if creator.mark_auto_delivery else '❌'}\n"
            message += f"📈 Автоподнятие: {'✅' if creator.auto_raise_lots else '❌'}"
            
            # Создаём inline keyboard с кнопками
            keyboard = K()
            
            # Кнопка подтвердить
            confirm_btn = B(
                text="✅ Создать шаблоны",
                callback_data=f"create_templates:{len(new_games)}"
            )
            
            # Кнопка отменить
            cancel_btn = B(
                text="❌ Отмена",
                callback_data="cancel_templates"
            )
            
            keyboard.row(confirm_btn, cancel_btn)
            
            # Сохраняем список игр в состоянии пользователя
            user_id = m.from_user.id
            chat_id = m.chat.id
            
            if chat_id not in self.user_states:
                self.user_states[chat_id] = {}
            
            self.user_states[chat_id][user_id] = {
                "state": "pending_template_creation",
                "data": new_games,
                "mid": None
            }
            
            self.bot.send_message(m.chat.id, message, reply_markup=keyboard)
            
        except Exception as ex:
            logger.error(f"Ошибка сканирования библиотеки: {ex}")
            self.bot.send_message(m.chat.id, f"❌ Ошибка сканирования: {str(ex)}")

    def add_game_to_list(self, m: Message):
        """
        Добавляет игру и создаёт все 3 типа шаблонов (оффлайн, почасовая, суточная).
        Использование: /add_game <название игры>
        """
        from auto_lot_creator import get_auto_lot_creator
        
        creator = get_auto_lot_creator()
        if not creator:
            self.bot.send_message(m.chat.id, "❌ AutoLotCreator не инициализирован.")
            return
        
        try:
            # Получаем название игры из сообщения
            args = m.text.split(maxsplit=1)
            if len(args) < 2:
                self.bot.send_message(m.chat.id, "❌ Укажите название игры. Пример: /add_game GTA V")
                return
            
            game_name = args[1].strip()
            
            self.bot.send_message(m.chat.id, f"🔄 Создаю шаблоны для {game_name}...")
            
            import configparser
            config_path = "configs/auto_delivery.cfg"
            
            config = configparser.ConfigParser()
            config.read(config_path, encoding='utf-8')
            
            # Создаём все 3 типа
            types_to_create = ["offline", "hourly", "daily"]
            
            created_count = 0
            for t in types_to_create:
                # Формируем название секции
                if t == "offline":
                    section_name = f"{game_name} Оффлайн активация"
                    lot_type_for_desc = "offline"
                elif t == "hourly":
                    section_name = f"{game_name} Аренда (почасовая)"
                    lot_type_for_desc = "rental"
                    creator.rental_hours = 1  # 1 час
                elif t == "daily":
                    section_name = f"{game_name} Аренда (суточная)"
                    lot_type_for_desc = "rental"
                    creator.rental_hours = 24  # 24 часа
                
                # Пропускаем если секция уже существует
                if config.has_section(section_name):
                    logger.info(f"Шаблон для '{section_name}' уже существует")
                    continue
                
                # Создаём секцию
                config.add_section(section_name)
                
                # Генерируем описание
                description = creator.generate_auto_delivery_template(game_name, lot_type_for_desc)
                config.set(section_name, "response", description)
                config.set(section_name, "telegramNotification", "1")
                config.set(section_name, "enabled", "1")
                
                created_count += 1
                logger.info(f"Создан шаблон для '{section_name}'")
            
            # Сохраняем конфиг
            with open(config_path, 'w', encoding='utf-8') as f:
                config.write(f)
            
            if created_count > 0:
                self.bot.send_message(m.chat.id, f"✅ Создано {created_count} шаблонов для {game_name}!")
                self.bot.send_message(m.chat.id, f"💡 Теперь создайте лоты на FunPay с названиями:\n• {game_name} Оффлайн активация\n• {game_name} Аренда (почасовая)\n• {game_name} Аренда (суточная)")
            else:
                self.bot.send_message(m.chat.id, "❌ Шаблоны уже существуют.")
            
        except Exception as ex:
            logger.error(f"Ошибка добавления игры: {ex}")
            import traceback
            logger.error(traceback.format_exc())
            self.bot.send_message(m.chat.id, f"❌ Ошибка добавления игры: {str(ex)}")

    def confirm_template_creation(self, c: CallbackQuery):
        """
        Подтверждает создание шаблонов.
        """
        from auto_lot_creator import get_auto_lot_creator
        
        try:
            self.bot.answer_callback_query(c.id)
            
            chat_id = c.message.chat.id
            user_id = c.from_user.id
            
            # Проверяем состояние пользователя
            if chat_id not in self.user_states or user_id not in self.user_states[chat_id]:
                self.bot.send_message(chat_id, "❌ Истекло время ожидания. Попробуйте снова.")
                return
            
            state = self.user_states[chat_id][user_id]
            if state.get("state") != "pending_template_creation":
                self.bot.send_message(chat_id, "❌ Неверное состояние.")
                return
            
            games = state.get("data", [])
            if not games:
                self.bot.send_message(chat_id, "❌ Нет игр для создания шаблонов.")
                return
            
            # Создаём шаблоны
            creator = get_auto_lot_creator()
            if not creator:
                self.bot.send_message(chat_id, "❌ AutoLotCreator не инициализирован.")
                return
            
            self.bot.send_message(chat_id, f"🔄 Создаю {len(games)} шаблонов...")
            
            # Создаём шаблоны (прямо в коде)
            import configparser
            config_path = "configs/auto_delivery.cfg"
            
            config = configparser.ConfigParser()
            config.read(config_path, encoding='utf-8')
            
            created_count = 0
            for game in games:
                game_name = game.get('name', 'Unknown Game')
                section_name = game_name
                
                # Пропускаем если секция уже существует
                if config.has_section(section_name):
                    logger.info(f"Шаблон для '{game_name}' уже существует")
                    continue
                
                # Создаём секцию
                config.add_section(section_name)
                
                # Генерируем описание
                description = creator.generate_auto_delivery_template(game_name, creator.lot_type)
                config.set(section_name, "response", description)
                config.set(section_name, "telegramNotification", "1")
                config.set(section_name, "enabled", "1")
                
                created_count += 1
                logger.info(f"Создан шаблон для '{game_name}'")
            
            # Сохраняем конфиг
            with open(config_path, 'w', encoding='utf-8') as f:
                config.write(f)
            
            # Очищаем состояние
            del self.user_states[chat_id][user_id]
            
            if created_count > 0:
                self.bot.send_message(chat_id, f"✅ Создано {created_count} шаблонов в auto_delivery.cfg!")
                self.bot.send_message(chat_id, f"💡 Теперь создайте лоты на FunPay с названиями: {', '.join([g.get('name') for g in games])}")
            else:
                self.bot.send_message(chat_id, "❌ Не удалось создать шаблоны.")
            
        except Exception as ex:
            logger.error(f"Ошибка подтверждения создания шаблонов: {ex}")
            self.bot.send_message(c.message.chat.id, f"❌ Ошибка: {str(ex)}")

    def cancel_template_creation(self, c: CallbackQuery):
        """
        Отменяет создание шаблонов.
        """
        try:
            self.bot.answer_callback_query(c.id)
            
            chat_id = c.message.chat.id
            user_id = c.from_user.id
            
            # Очищаем состояние
            if chat_id in self.user_states and user_id in self.user_states[chat_id]:
                del self.user_states[chat_id][user_id]
            
            self.bot.send_message(chat_id, "❌ Создание шаблонов отменено.")
            
        except Exception as ex:
            logger.error(f"Ошибка отмены создания шаблонов: {ex}")

    def test_callback(self, m: Message):
        """Тестовая команда для проверки callback"""
        keyboard = K()
        test_btn = B(text="Test Button", callback_data=CBT.EMPTY)
        keyboard.row(test_btn)
        self.bot.send_message(m.chat.id, "Test callback", reply_markup=keyboard)

    def create_game_template(self, c: CallbackQuery):
        """
        Создаёт шаблон для игры с выбранным типом лота.
        """
        from auto_lot_creator import get_auto_lot_creator
        
        try:
            logger.info(f"Получен callback: {c.data}")
            self.bot.answer_callback_query(c.id)
            
            chat_id = c.message.chat.id
            user_id = c.from_user.id
            
            # Парсим callback_data
            data_parts = c.data.split(":")
            logger.info(f"Data parts: {data_parts}")
            
            if len(data_parts) < 3:
                self.bot.send_message(chat_id, "❌ Неверный формат данных.")
                return
            
            game_name = data_parts[1]
            lot_type = data_parts[2]  # offline, hourly, daily, all
            
            logger.info(f"Создание шаблона для игры: {game_name}, тип: {lot_type}")
            
            # Очищаем состояние
            if chat_id in self.user_states and user_id in self.user_states[chat_id]:
                del self.user_states[chat_id][user_id]
            
            # Создаём шаблоны
            creator = get_auto_lot_creator()
            if not creator:
                self.bot.send_message(chat_id, "❌ AutoLotCreator не инициализирован.")
                return
            
            self.bot.send_message(chat_id, f"🔄 Создаю шаблон для {game_name}...")
            
            import configparser
            config_path = "configs/auto_delivery.cfg"
            
            config = configparser.ConfigParser()
            config.read(config_path, encoding='utf-8')
            
            # Определяем какие типы создавать
            types_to_create = []
            if lot_type == "all":
                types_to_create = ["offline", "hourly", "daily"]
            else:
                types_to_create = [lot_type]
            
            logger.info(f"Типы для создания: {types_to_create}")
            
            created_count = 0
            for t in types_to_create:
                # Формируем название секции
                if t == "offline":
                    section_name = f"{game_name} Оффлайн активация"
                    lot_type_for_desc = "offline"
                elif t == "hourly":
                    section_name = f"{game_name} Аренда (почасовая)"
                    lot_type_for_desc = "rental"
                    creator.rental_hours = 1  # 1 час
                elif t == "daily":
                    section_name = f"{game_name} Аренда (суточная)"
                    lot_type_for_desc = "rental"
                    creator.rental_hours = 24  # 24 часа
                
                logger.info(f"Создание секции: {section_name}")
                
                # Пропускаем если секция уже существует
                if config.has_section(section_name):
                    logger.info(f"Шаблон для '{section_name}' уже существует")
                    continue
                
                # Создаём секцию
                config.add_section(section_name)
                
                # Генерируем описание
                description = creator.generate_auto_delivery_template(game_name, lot_type_for_desc)
                config.set(section_name, "response", description)
                config.set(section_name, "telegramNotification", "1")
                config.set(section_name, "enabled", "1")
                
                created_count += 1
                logger.info(f"Создан шаблон для '{section_name}'")
            
            # Сохраняем конфиг
            with open(config_path, 'w', encoding='utf-8') as f:
                config.write(f)
            
            logger.info(f"Сохранено {created_count} шаблонов")
            
            if created_count > 0:
                type_names = {
                    "offline": "Оффлайн активация",
                    "hourly": "Аренда почасовая",
                    "daily": "Аренда суточная"
                }
                
                if lot_type == "all":
                    created_types = "все типы (оффлайн, почасовая, суточная)"
                else:
                    created_types = type_names.get(lot_type, lot_type)
                
                self.bot.send_message(chat_id, f"✅ Создано {created_count} шаблонов ({created_types}) в auto_delivery.cfg!")
                self.bot.send_message(chat_id, f"💡 Теперь создайте лот на FunPay с названием: {', '.join([f'{game_name} ({type_names.get(t, t)})' for t in types_to_create])}")
            else:
                self.bot.send_message(chat_id, "❌ Шаблоны уже существуют.")
            
        except Exception as ex:
            logger.error(f"Ошибка создания шаблона: {ex}")
            import traceback
            logger.error(traceback.format_exc())
            self.bot.send_message(c.message.chat.id, f"❌ Ошибка: {str(ex)}")

    def ask_power_off(self, m: Message):
        """
        Просит подтверждение на отключение FPC.
        """
        self.bot.send_message(m.chat.id, _("power_off_0"), reply_markup=kb.power_off(self.cardinal.instance_id, 0))

    def cancel_power_off(self, c: CallbackQuery):
        """
        Отменяет выключение (удаляет клавиатуру с кнопками подтверждения).
        """
        self.bot.edit_message_text(_("power_off_cancelled"), c.message.chat.id, c.message.id)
        self.bot.answer_callback_query(c.id)

    def power_off(self, c: CallbackQuery):
        """
        Отключает FPC.
        """
        split = c.data.split(":")
        state = int(split[1])
        instance_id = int(split[2])

        if instance_id != self.cardinal.instance_id:
            self.bot.edit_message_text(_("power_off_error"), c.message.chat.id, c.message.id)
            self.bot.answer_callback_query(c.id)
            return

        if state == 6:
            self.bot.edit_message_text(_("power_off_6"), c.message.chat.id, c.message.id)
            self.bot.answer_callback_query(c.id)
            cardinal_tools.shut_down()
            return

        self.bot.edit_message_text(_(f"power_off_{state}"), c.message.chat.id, c.message.id,
                                   reply_markup=kb.power_off(instance_id, state))
        self.bot.answer_callback_query(c.id)

    # Чат FunPay
    def act_send_funpay_message(self, c: CallbackQuery):
        """
        Активирует режим ввода сообщения для отправки его в чат FunPay.
        """
        split = c.data.split(":")
        node_id = int(split[1])
        try:
            username = split[2]
        except IndexError:
            username = None
        result = self.bot.send_message(c.message.chat.id, _("enter_msg_text"), reply_markup=skb.CLEAR_STATE_BTN())
        self.set_state(c.message.chat.id, result.id, c.from_user.id,
                       CBT.SEND_FP_MESSAGE, {"node_id": node_id, "username": username})
        self.bot.answer_callback_query(c.id)

    def send_funpay_message(self, message: Message):
        """
        Отправляет сообщение в чат FunPay.
        """
        data = self.get_state(message.chat.id, message.from_user.id)["data"]
        node_id, username = data["node_id"], data["username"]
        self.clear_state(message.chat.id, message.from_user.id, True)
        response_text = message.text.strip()
        result = self.cardinal.send_message(node_id, response_text, username, watermark=False)
        if result:
            self.bot.reply_to(message, _("msg_sent", node_id, username),
                              reply_markup=kb.reply(node_id, username, again=True, extend=True))
        else:
            self.bot.reply_to(message, _("msg_sending_error", node_id, username),
                              reply_markup=kb.reply(node_id, username, again=True, extend=True))

    def act_upload_image(self, m: Message):
        """
        Активирует режим ожидания изображения для последующей выгрузки на FunPay.
        """
        cbt = CBT.UPLOAD_CHAT_IMAGE if m.text.startswith("/upload_chat_img") else CBT.UPLOAD_OFFER_IMAGE
        result = self.bot.send_message(m.chat.id, _("send_img"), reply_markup=skb.CLEAR_STATE_BTN())
        self.set_state(m.chat.id, result.id, m.from_user.id, cbt)

    def act_upload_backup(self, m: Message):
        """
        Активирует режим ожидания бэкапа.
        """
        result = self.bot.send_message(m.chat.id, _("send_backup"), reply_markup=skb.CLEAR_STATE_BTN())
        self.set_state(m.chat.id, result.id, m.from_user.id, CBT.UPLOAD_BACKUP)

    def act_edit_greetings_text(self, c: CallbackQuery):
        variables = ["v_date", "v_date_text", "v_full_date_text", "v_time", "v_full_time", "v_username",
                     "v_message_text", "v_chat_id", "v_chat_name", "v_photo", "v_sleep"]
        text = f"{_('v_edit_greeting_text')}\n\n{_('v_list')}:\n" + "\n".join(_(i) for i in variables)
        result = self.bot.send_message(c.message.chat.id, text, reply_markup=skb.CLEAR_STATE_BTN())
        self.set_state(c.message.chat.id, result.id, c.from_user.id, CBT.EDIT_GREETINGS_TEXT)
        self.bot.answer_callback_query(c.id)

    def edit_greetings_text(self, m: Message):
        self.clear_state(m.chat.id, m.from_user.id, True)
        self.cardinal.MAIN_CFG["Greetings"]["greetingsText"] = m.text
        logger.info(_("log_greeting_changed", m.from_user.username, m.from_user.id, m.text))
        self.cardinal.save_config(self.cardinal.MAIN_CFG, "configs/_main.cfg")
        keyboard = K() \
            .row(B(_("gl_back"), callback_data=f"{CBT.CATEGORY}:gr"),
                 B(_("gl_edit"), callback_data=CBT.EDIT_GREETINGS_TEXT))
        self.bot.reply_to(m, _("greeting_changed"), reply_markup=keyboard)

    def act_edit_greetings_cooldown(self, c: CallbackQuery):
        text = _('v_edit_greeting_cooldown')
        result = self.bot.send_message(c.message.chat.id, text, reply_markup=skb.CLEAR_STATE_BTN())
        self.set_state(c.message.chat.id, result.id, c.from_user.id, CBT.EDIT_GREETINGS_COOLDOWN)
        self.bot.answer_callback_query(c.id)

    def edit_greetings_cooldown(self, m: Message):
        self.clear_state(m.chat.id, m.from_user.id, True)
        try:
            cooldown = float(m.text)
        except:
            self.bot.reply_to(m, _("gl_error_try_again"))
            return
        self.cardinal.MAIN_CFG["Greetings"]["greetingsCooldown"] = str(cooldown)
        logger.info(_("log_greeting_cooldown_changed", m.from_user.username, m.from_user.id, m.text))
        self.cardinal.save_config(self.cardinal.MAIN_CFG, "configs/_main.cfg")
        keyboard = K() \
            .row(B(_("gl_back"), callback_data=f"{CBT.CATEGORY}:gr"),
                 B(_("gl_edit"), callback_data=CBT.EDIT_GREETINGS_COOLDOWN))
        self.bot.reply_to(m, _("greeting_cooldown_changed").format(m.text), reply_markup=keyboard)

    def act_edit_order_confirm_reply_text(self, c: CallbackQuery):
        variables = ["v_date", "v_date_text", "v_full_date_text", "v_time", "v_full_time", "v_username",
                     "v_order_id", "v_order_link", "v_order_title", "v_game", "v_category", "v_category_fullname",
                     "v_photo", "v_sleep"]
        text = f"{_('v_edit_order_confirm_text')}\n\n{_('v_list')}:\n" + "\n".join(_(i) for i in variables)
        result = self.bot.send_message(c.message.chat.id, text, reply_markup=skb.CLEAR_STATE_BTN())
        self.set_state(c.message.chat.id, result.id, c.from_user.id, CBT.EDIT_ORDER_CONFIRM_REPLY_TEXT)
        self.bot.answer_callback_query(c.id)

    def edit_order_confirm_reply_text(self, m: Message):
        self.clear_state(m.chat.id, m.from_user.id, True)
        self.cardinal.MAIN_CFG["OrderConfirm"]["replyText"] = m.text
        logger.info(_("log_order_confirm_changed", m.from_user.username, m.from_user.id, m.text))
        self.cardinal.save_config(self.cardinal.MAIN_CFG, "configs/_main.cfg")
        keyboard = K() \
            .row(B(_("gl_back"), callback_data=f"{CBT.CATEGORY}:oc"),
                 B(_("gl_edit"), callback_data=CBT.EDIT_ORDER_CONFIRM_REPLY_TEXT))
        self.bot.reply_to(m, _("order_confirm_changed"), reply_markup=keyboard)

    def act_edit_review_reply_text(self, c: CallbackQuery):
        stars = int(c.data.split(":")[1])
        variables = ["v_date", "v_date_text", "v_full_date_text", "v_time", "v_full_time", "v_username",
                     "v_order_id", "v_order_link", "v_order_title", "v_order_params",
                     "v_order_desc_and_params", "v_order_desc_or_params", "v_game", "v_category", "v_category_fullname"]
        text = f"{_('v_edit_review_reply_text', '⭐' * stars)}\n\n{_('v_list')}:\n" + "\n".join(_(i) for i in variables)
        result = self.bot.send_message(c.message.chat.id, text, reply_markup=skb.CLEAR_STATE_BTN())
        self.set_state(c.message.chat.id, result.id, c.from_user.id, CBT.EDIT_REVIEW_REPLY_TEXT, {"stars": stars})
        self.bot.answer_callback_query(c.id)

    def edit_review_reply_text(self, m: Message):
        stars = self.get_state(m.chat.id, m.from_user.id)["data"]["stars"]
        self.clear_state(m.chat.id, m.from_user.id, True)
        self.cardinal.MAIN_CFG["ReviewReply"][f"star{stars}ReplyText"] = m.text
        logger.info(_("log_review_reply_changed", m.from_user.username, m.from_user.id, stars, m.text))
        self.cardinal.save_config(self.cardinal.MAIN_CFG, "configs/_main.cfg")
        keyboard = K() \
            .row(B(_("gl_back"), callback_data=f"{CBT.CATEGORY}:rr"),
                 B(_("gl_edit"), callback_data=f"{CBT.EDIT_REVIEW_REPLY_TEXT}:{stars}"))
        self.bot.reply_to(m, _("review_reply_changed", '⭐' * stars), reply_markup=keyboard)

    def open_reply_menu(self, c: CallbackQuery):
        """
        Открывает меню ответа на сообщение (callback используется в кнопках "назад").
        """
        split = c.data.split(":")
        node_id, username, again = int(split[1]), split[2], int(split[3])
        extend = True if len(split) > 4 and int(split[4]) else False
        self.bot.edit_message_reply_markup(c.message.chat.id, c.message.id,
                                           reply_markup=kb.reply(node_id, username, bool(again), extend))

    def extend_new_message_notification(self, c: CallbackQuery):
        """
        "Расширяет" уведомление о новом сообщении.
        """
        chat_id, username = c.data.split(":")[1:]
        try:
            chat = self.cardinal.account.get_chat(int(chat_id))
        except:
            self.bot.answer_callback_query(c.id)
            self.bot.send_message(c.message.chat.id, _("get_chat_error"))
            return

        text = ""
        if chat.looking_link:
            text += f"<b><i>{_('viewing')}:</i></b>\n<a href=\"{chat.looking_link}\">{chat.looking_text}</a>\n\n"

        text += utils.format_messages(self.cardinal, chat.messages[-10:])

        self.bot.edit_message_text(text, c.message.chat.id, c.message.id,
                                   reply_markup=kb.reply(int(chat_id), username, False, False))

    # Ордер
    def ask_confirm_refund(self, call: CallbackQuery):
        """
        Просит подтвердить возврат денег.
        """
        split = call.data.split(":")
        order_id, node_id, username = split[1], int(split[2]), split[3]
        keyboard = kb.new_order(order_id, username, node_id, confirmation=True)
        self.bot.edit_message_reply_markup(call.message.chat.id, call.message.id, reply_markup=keyboard)
        self.bot.answer_callback_query(call.id)

    def cancel_refund(self, call: CallbackQuery):
        """
        Отменяет возврат.
        """
        split = call.data.split(":")
        order_id, node_id, username = split[1], int(split[2]), split[3]
        keyboard = kb.new_order(order_id, username, node_id)
        self.bot.edit_message_reply_markup(call.message.chat.id, call.message.id, reply_markup=keyboard)
        self.bot.answer_callback_query(call.id)

    def refund(self, c: CallbackQuery):
        """
        Оформляет возврат за заказ.
        """
        split = c.data.split(":")
        order_id, node_id, username = split[1], int(split[2]), split[3]
        new_msg = None
        attempts = 3
        while attempts:
            try:
                self.cardinal.account.refund(order_id)
                break
            except:
                if not new_msg:
                    new_msg = self.bot.send_message(c.message.chat.id, _("refund_attempt", order_id, attempts))
                else:
                    self.bot.edit_message_text(_("refund_attempt", order_id, attempts), new_msg.chat.id, new_msg.id)
                attempts -= 1
                time.sleep(1)

        else:
            self.bot.edit_message_text(_("refund_error", order_id), new_msg.chat.id, new_msg.id)

            keyboard = kb.new_order(order_id, username, node_id)
            self.bot.edit_message_reply_markup(c.message.chat.id, c.message.id, reply_markup=keyboard)
            self.bot.answer_callback_query(c.id)
            return

        if not new_msg:
            self.bot.send_message(c.message.chat.id, _("refund_complete", order_id))
        else:
            self.bot.edit_message_text(_("refund_complete", order_id), new_msg.chat.id, new_msg.id)

        keyboard = kb.new_order(order_id, username, node_id, no_refund=True)
        self.bot.edit_message_reply_markup(c.message.chat.id, c.message.id, reply_markup=keyboard)
        self.bot.answer_callback_query(c.id)

    def open_order_menu(self, c: CallbackQuery):
        split = c.data.split(":")
        node_id, username, order_id, no_refund = int(split[1]), split[2], split[3], bool(int(split[4]))
        self.bot.edit_message_reply_markup(c.message.chat.id, c.message.id,
                                           reply_markup=kb.new_order(order_id, username, node_id, no_refund=no_refund))

    # Панель управления
    def open_cp(self, c: CallbackQuery):
        """
        Открывает основное меню настроек (редактирует сообщение).
        """
        self.bot.edit_message_text(_("desc_main"), c.message.chat.id, c.message.id,
                                   reply_markup=skb.SETTINGS_SECTIONS())
        self.bot.answer_callback_query(c.id)

    def open_cp2(self, c: CallbackQuery):
        """
        Открывает 2 страницу основного меню настроек (редактирует сообщение).
        """
        self.bot.edit_message_text(_("desc_main"), c.message.chat.id, c.message.id,
                                   reply_markup=skb.SETTINGS_SECTIONS_2())
        self.bot.answer_callback_query(c.id)

    def switch_param(self, c: CallbackQuery):
        """
        Переключает переключаемые настройки FPC.
        """
        split = c.data.split(":")
        section, option = split[1], split[2]
        if section == "FunPay" and option == "oldMsgGetMode":
            self.cardinal.switch_msg_get_mode()
        else:
            self.cardinal.MAIN_CFG[section][option] = str(int(not int(self.cardinal.MAIN_CFG[section][option])))
            self.cardinal.save_config(self.cardinal.MAIN_CFG, "configs/_main.cfg")

        sections = {
            "FunPay": kb.main_settings,
            "BlockList": kb.blacklist_settings,
            "NewMessageView": kb.new_message_view_settings,
            "Greetings": kb.greeting_settings,
            "OrderConfirm": kb.order_confirm_reply_settings,
            "ReviewReply": kb.review_reply_settings
        }
        if section == "Telegram":
            self.bot.edit_message_reply_markup(c.message.chat.id, c.message.id,
                                               reply_markup=kb.authorized_users(self.cardinal, offset=int(split[3])))
        else:
            self.bot.edit_message_reply_markup(c.message.chat.id, c.message.id,
                                               reply_markup=sections[section](self.cardinal))
        logger.info(_("log_param_changed", c.from_user.username, c.from_user.id, option, section,
                      self.cardinal.MAIN_CFG[section][option]))
        self.bot.answer_callback_query(c.id)

    def switch_chat_notification(self, c: CallbackQuery):
        split = c.data.split(":")
        chat_id, notification_type = int(split[1]), split[2]

        result = self.toggle_notification(chat_id, notification_type)
        logger.info(_("log_notification_switched", c.from_user.username, c.from_user.id,
                      notification_type, c.message.chat.id, result))
        keyboard = kb.announcements_settings if notification_type in [utils.NotificationTypes.announcement,
                                                                      utils.NotificationTypes.ad] \
            else kb.notifications_settings
        self.bot.edit_message_reply_markup(c.message.chat.id, c.message.id,
                                           reply_markup=keyboard(self.cardinal, c.message.chat.id))
        self.bot.answer_callback_query(c.id)

    def open_settings_section(self, c: CallbackQuery):
        """
        Открывает выбранную категорию настроек.
        """
        #
        section = c.data.split(":")[1]
        sections = {
            "lang": (_("desc_lang"), kb.language_settings, [self.cardinal]),
            "main": (_("desc_gs"), kb.main_settings, [self.cardinal]),
            "tg": (_("desc_ns", c.message.chat.id), kb.notifications_settings, [self.cardinal, c.message.chat.id]),
            "bl": (_("desc_bl"), kb.blacklist_settings, [self.cardinal]),
            "ar": (_("desc_ar"), skb.AR_SETTINGS, []),
            "ad": (_("desc_ad"), skb.AD_SETTINGS, []),
            "mv": (_("desc_mv"), kb.new_message_view_settings, [self.cardinal]),
            "rr": (_("desc_or"), kb.review_reply_settings, [self.cardinal]),
            "gr": (_("desc_gr", utils.escape(self.cardinal.MAIN_CFG['Greetings']['greetingsText'])),
                   kb.greeting_settings, [self.cardinal]),
            "oc": (_("desc_oc", utils.escape(self.cardinal.MAIN_CFG['OrderConfirm']['replyText'])),
                   kb.order_confirm_reply_settings, [self.cardinal])
        }

        curr = sections[section]
        self.bot.edit_message_text(curr[0], c.message.chat.id, c.message.id, reply_markup=curr[1](*curr[2]))
        self.bot.answer_callback_query(c.id)

    # Прочее
    def cancel_action(self, call: CallbackQuery):
        """
        Обнуляет состояние пользователя по кнопке "Отмена" (CBT.CLEAR_STATE).
        """
        result = self.clear_state(call.message.chat.id, call.from_user.id, True)
        if result is None:
            self.bot.answer_callback_query(call.id)

    def param_disabled(self, c: CallbackQuery):
        """
        Отправляет сообщение о том, что параметр отключен в глобальных переключателях.
        """
        self.bot.answer_callback_query(c.id, _("param_disabled"), show_alert=True)

    def send_announcements_kb(self, m: Message):
        """
        Отправляет сообщение с клавиатурой управления уведомлениями о новых объявлениях.
        """
        self.bot.send_message(m.chat.id, _("desc_an"), reply_markup=kb.announcements_settings(self.cardinal, m.chat.id))

    def send_review_reply_text(self, c: CallbackQuery):
        stars = int(c.data.split(":")[1])
        text = self.cardinal.MAIN_CFG["ReviewReply"][f"star{stars}ReplyText"]
        keyboard = K() \
            .row(B(_("gl_back"), callback_data=f"{CBT.CATEGORY}:rr"),
                 B(_("gl_edit"), callback_data=f"{CBT.EDIT_REVIEW_REPLY_TEXT}:{stars}"))
        if not text:
            self.bot.send_message(c.message.chat.id, _("review_reply_empty", "⭐" * stars), reply_markup=keyboard)
        else:
            self.bot.send_message(c.message.chat.id, _("review_reply_text", "⭐" * stars,
                                                       self.cardinal.MAIN_CFG['ReviewReply'][f'star{stars}ReplyText']),
                                  reply_markup=keyboard)
        self.bot.answer_callback_query(c.id)

    def send_old_mode_help_text(self, c: CallbackQuery):
        self.bot.answer_callback_query(c.id)
        self.bot.send_message(c.message.chat.id, _("old_mode_help"))

    def empty_callback(self, c: CallbackQuery):
        self.bot.answer_callback_query(c.id, "🤑 @sidor_donate 🤑")

    def switch_lang(self, c: CallbackQuery):
        lang = c.data.split(":")[1]
        Localizer(lang)
        self.cardinal.MAIN_CFG["Other"]["language"] = lang
        self.cardinal.save_config(self.cardinal.MAIN_CFG, "configs/_main.cfg")
        if localizer.current_language == "en":
            self.bot.answer_callback_query(c.id, "The translation may be incomplete and contain errors.\n\n"
                                                 "If you find errors in the translation, let @sidor0912 know.\n\n"
                                                 "Thank you :)", show_alert=True)
        elif localizer.current_language == "uk":
            self.bot.answer_callback_query(c.id, "Переклад складено за допомогою ChatGPT.\n"
                                                 "Повідомте @sidor0912, якщо знайдете помилки.", show_alert=True)
        elif localizer.current_language == "ru":
            self.bot.answer_callback_query(c.id, '«А я сейчас вам покажу, откуда на Беларусь готовилось нападение»',
                                           show_alert=True)
        c.data = f"{CBT.CATEGORY}:lang"
        self.open_settings_section(c)

    def __register_handlers(self):
        """
        Регистрирует хэндлеры всех команд.
        """
        self.mdw_handler(self.setup_chat_notifications, update_types=['message'])
        self.msg_handler(self.reg_admin, func=lambda msg: msg.from_user.id not in self.authorized_users,
                         content_types=['text', 'document', 'photo', 'sticker'])
        self.cbq_handler(self.ignore_unauthorized_users, lambda c: c.from_user.id not in self.authorized_users)
        self.cbq_handler(self.param_disabled, lambda c: c.data.startswith(CBT.PARAM_DISABLED))
        self.msg_handler(self.run_file_handlers, content_types=["photo", "document"],
                         func=lambda m: self.is_file_handler(m))

        self.msg_handler(self.send_settings_menu, commands=["menu", "start"])
        self.msg_handler(self.send_profile, commands=["profile"])
        self.msg_handler(self.act_change_cookie, commands=["change_cookie", "golden_key"])
        self.msg_handler(self.change_cookie, func=lambda m: self.check_state(m.chat.id, m.from_user.id,
                                                                             CBT.CHANGE_GOLDEN_KEY))
        self.cbq_handler(self.update_profile, lambda c: c.data == CBT.UPDATE_PROFILE)
        self.msg_handler(self.act_manual_delivery_test, commands=["test_lot"])
        self.msg_handler(self.act_upload_image, commands=["upload_chat_img", "upload_offer_img"])
        self.msg_handler(self.act_upload_backup, commands=["upload_backup"])
        self.cbq_handler(self.act_edit_greetings_text, lambda c: c.data == CBT.EDIT_GREETINGS_TEXT)
        self.msg_handler(self.edit_greetings_text,
                         func=lambda m: self.check_state(m.chat.id, m.from_user.id, CBT.EDIT_GREETINGS_TEXT))
        self.cbq_handler(self.act_edit_greetings_cooldown, lambda c: c.data == CBT.EDIT_GREETINGS_COOLDOWN)
        self.msg_handler(self.edit_greetings_cooldown,
                         func=lambda m: self.check_state(m.chat.id, m.from_user.id, CBT.EDIT_GREETINGS_COOLDOWN))
        self.cbq_handler(self.act_edit_order_confirm_reply_text, lambda c: c.data == CBT.EDIT_ORDER_CONFIRM_REPLY_TEXT)
        self.msg_handler(self.edit_order_confirm_reply_text,
                         func=lambda m: self.check_state(m.chat.id, m.from_user.id, CBT.EDIT_ORDER_CONFIRM_REPLY_TEXT))
        self.cbq_handler(self.act_edit_review_reply_text, lambda c: c.data.startswith(f"{CBT.EDIT_REVIEW_REPLY_TEXT}:"))
        self.msg_handler(self.edit_review_reply_text,
                         func=lambda m: self.check_state(m.chat.id, m.from_user.id, CBT.EDIT_REVIEW_REPLY_TEXT))
        self.msg_handler(self.manual_delivery_text,
                         func=lambda m: self.check_state(m.chat.id, m.from_user.id, CBT.MANUAL_AD_TEST))
        self.msg_handler(self.act_ban, commands=["ban"])
        self.msg_handler(self.ban, func=lambda m: self.check_state(m.chat.id, m.from_user.id, CBT.BAN))
        self.msg_handler(self.act_unban, commands=["unban"])
        self.msg_handler(self.unban, func=lambda m: self.check_state(m.chat.id, m.from_user.id, CBT.UNBAN))
        self.msg_handler(self.send_ban_list, commands=["black_list"])
        self.msg_handler(self.act_edit_watermark, commands=["watermark"])
        self.msg_handler(self.edit_watermark,
                         func=lambda m: self.check_state(m.chat.id, m.from_user.id, CBT.EDIT_WATERMARK))
        self.msg_handler(self.send_logs, commands=["logs"])
        self.msg_handler(self.del_logs, commands=["del_logs"])
        self.msg_handler(self.about, commands=["about"])
        self.msg_handler(self.check_updates, commands=["check_updates"])
        self.msg_handler(self.update, commands=["update"])
        self.msg_handler(self.get_backup, commands=["get_backup"])
        self.msg_handler(self.create_backup, commands=["create_backup"])
        self.msg_handler(self.send_system_info, commands=["sys"])
        self.msg_handler(self.restart_cardinal, commands=["restart"])
        self.msg_handler(self.ask_power_off, commands=["power_off"])
        self.msg_handler(self.send_announcements_kb, commands=["announcements"])
        self.msg_handler(self.tg_guard_code, commands=["code"])
        self.msg_handler(self.add_game_to_list, commands=["add_game"])
        self.msg_handler(self.tg_refund, commands=["refund"])
        self.cbq_handler(self.send_review_reply_text, lambda c: c.data.startswith(f"{CBT.SEND_REVIEW_REPLY_TEXT}:"))

        self.cbq_handler(self.act_send_funpay_message, lambda c: c.data.startswith(f"{CBT.SEND_FP_MESSAGE}:"))
        self.cbq_handler(self.open_reply_menu, lambda c: c.data.startswith(f"{CBT.BACK_TO_REPLY_KB}:"))
        self.cbq_handler(self.extend_new_message_notification, lambda c: c.data.startswith(f"{CBT.EXTEND_CHAT}:"))
        self.msg_handler(self.send_funpay_message,
                         func=lambda m: self.check_state(m.chat.id, m.from_user.id, CBT.SEND_FP_MESSAGE))
        self.cbq_handler(self.ask_confirm_refund, lambda c: c.data.startswith(f"{CBT.REQUEST_REFUND}:"))
        self.cbq_handler(self.cancel_refund, lambda c: c.data.startswith(f"{CBT.REFUND_CANCELLED}:"))
        self.cbq_handler(self.refund, lambda c: c.data.startswith(f"{CBT.REFUND_CONFIRMED}:"))
        self.cbq_handler(self.open_order_menu, lambda c: c.data.startswith(f"{CBT.BACK_TO_ORDER_KB}:"))
        self.cbq_handler(self.open_cp, lambda c: c.data == CBT.MAIN)
        self.cbq_handler(self.open_cp2, lambda c: c.data == CBT.MAIN2)
        self.cbq_handler(self.open_settings_section, lambda c: c.data.startswith(f"{CBT.CATEGORY}:"))
        self.cbq_handler(self.switch_param, lambda c: c.data.startswith(f"{CBT.SWITCH}:"))
        self.cbq_handler(self.switch_chat_notification, lambda c: c.data.startswith(f"{CBT.SWITCH_TG_NOTIFICATIONS}:"))
        self.cbq_handler(self.power_off, lambda c: c.data.startswith(f"{CBT.SHUT_DOWN}:"))
        self.cbq_handler(self.cancel_power_off, lambda c: c.data == CBT.CANCEL_SHUTTING_DOWN)
        self.cbq_handler(self.cancel_action, lambda c: c.data == CBT.CLEAR_STATE)
        self.cbq_handler(self.send_old_mode_help_text, lambda c: c.data == CBT.OLD_MOD_HELP)
        self.cbq_handler(self.empty_callback, lambda c: c.data == CBT.EMPTY)
        self.cbq_handler(self.switch_lang, lambda c: c.data.startswith(f"{CBT.LANG}:"))

    def send_notification(self, text: str | None, keyboard: K | None = None,
                          notification_type: str = utils.NotificationTypes.other, photo: bytes | None = None,
                          pin: bool = False):
        """
        Отправляет сообщение во все чаты для уведомлений из self.notification_settings.

        :param text: текст уведомления.
        :param keyboard: экземпляр клавиатуры.
        :param notification_type: тип уведомления.
        :param photo: фотография (если нужна).
        :param pin: закреплять ли сообщение.
        """
        kwargs = {}
        if keyboard is not None:
            kwargs["reply_markup"] = keyboard
        to_delete = []
        for chat_id in self.notification_settings:
            if notification_type != utils.NotificationTypes.important_announcement and \
                    not self.is_notification_enabled(chat_id, notification_type):
                continue

            try:
                if photo:
                    msg = self.bot.send_photo(chat_id, photo, text, **kwargs)
                else:
                    msg = self.bot.send_message(chat_id, text, **kwargs)

                if notification_type == utils.NotificationTypes.bot_start:
                    self.init_messages.append((msg.chat.id, msg.id))

                if pin:
                    self.bot.pin_chat_message(msg.chat.id, msg.id)
            except Exception as e:
                logger.error(_("log_tg_notification_error", chat_id))
                logger.debug("TRACEBACK", exc_info=True)
                if isinstance(e, ApiTelegramException) and (
                        e.result.status_code == 403 or e.result.status_code == 400 and
                        (e.result_json.get('description') in \
                         ("Bad Request: group chat was upgraded to a supergroup chat", "Bad Request: chat not found"))):
                    to_delete.append(chat_id)
                continue
        for chat_id in to_delete:
            if chat_id in self.notification_settings:
                del self.notification_settings[chat_id]
                utils.save_notification_settings(self.notification_settings)

    def add_command_to_menu(self, command: str, help_text: str) -> None:
        """
        Добавляет команду в список команд (в кнопке menu).

        :param command: текст команды.

        :param help_text: текст справки.
        """
        self.commands[command] = help_text

    def setup_commands(self):
        """
        Устанавливает меню команд.
        """
        for lang in (None, *localizer.languages.keys()):
            commands = [BotCommand(f"/{i}", _(self.commands[i], language=lang)) for i in self.commands]
            self.bot.set_my_commands(commands, language_code=lang)

    def edit_bot(self):
        """
        Изменяет описания и название бота.
        """

        name = self.bot.get_me().full_name
        limit = 64
        add_to_name = ["FunPay Bot | Бот ФанПей", "FunPay Bot", "FunPayBot", "FunPay"]
        new_name = name
        if "vertex" in new_name.lower():
            new_name = ""
        new_name = new_name.split("ㅤ")[0].strip()
        if "funpay" not in new_name.lower():
            for m_name in add_to_name:
                if len(new_name) + 2 + len(m_name) <= limit:
                    new_name = f"{(new_name + ' ').ljust(limit - len(m_name) - 1, 'ㅤ')} {m_name}"
                    break
            if new_name != name:
                self.bot.set_my_name(new_name)
        sh_text = "🛠️ github.com/sidor0912/FunPayCardinal 💰 @sidor_donate 👨‍💻 @sidor0912 🧩 @fpc_plugins 🔄 @fpc_updates 💬 @funpay_cardinal"
        res = self.bot.get_my_short_description().short_description
        if res != sh_text:
            self.bot.set_my_short_description(sh_text)
        for i in [None, *localizer.languages.keys()]:
            res = self.bot.get_my_description(i).description
            text = _("adv_description", self.cardinal.VERSION, language=i)
            if res != text:
                self.bot.set_my_description(text, language_code=i)

    def init(self):
        self.__register_handlers()
        logger.info(_("log_tg_initialized"))

    def run(self):
        """
        Запускает поллинг.
        """
        self.send_notification(_("bot_started"), notification_type=utils.NotificationTypes.bot_start)
        k_err = 0
        while True:
            try:
                logger.info(_("log_tg_started", self.bot.user.username))
                self.bot.infinity_polling(logger_level=logging.DEBUG,
                                          timeout=20, long_polling_timeout=20,
                                          allowed_updates=["message", "callback_query", "chat_member"],
                                          skip_pending=True)
            except:
                k_err += 1
                logger.error(_("log_tg_update_error", k_err))
                logger.debug("TRACEBACK", exc_info=True)
                time.sleep(10)
