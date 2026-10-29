"""
В данном модуле написаны хэндлеры для разных эвентов.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cardinal import Cardinal

from FunPayAPI.types import OrderShortcut, Order
from FunPayAPI import exceptions, utils as fp_utils
from FunPayAPI.updater.events import *

from tg_bot import utils, keyboards
from Utils import cardinal_tools
from locales.localizer import Localizer
from threading import Thread
import configparser
from datetime import datetime
import logging
import time
import re
import purchase_checker
import form_processor
import rental_system

LAST_STACK_ID = ""
MSG_LOG_LAST_STACK_ID = ""

logger = logging.getLogger("FPC.handlers")
localizer = Localizer()
_ = localizer.translate


def _parse_steam_login(product_line: str) -> str | None:
    """Парсит Steam-логин из строки товара формата login:password[:steamid]."""
    if not product_line:
        return None
    parts = product_line.split(":")
    if len(parts) >= 2 and parts[0].strip():
        return parts[0].strip()
    return None

ORDER_HTML_TEMPLATE = """<a href="https://funpay.com/orders/DELITEST/" class="tc-item">
   <div class="tc-date" bis_skin_checked="1">
      <div class="tc-date-time" bis_skin_checked="1">сегодня, $date</div>
      <div class="tc-date-left" bis_skin_checked="1">только что</div>
   </div>
   <div class="tc-order" bis_skin_checked="1">#DELITEST</div>
   <div class="order-desc" bis_skin_checked="1">
      <div bis_skin_checked="1">$lot_name</div>
      <div class="text-muted" bis_skin_checked="1">Автовыдача, Тест</div>
   </div>
   <div class="tc-user" bis_skin_checked="1">
      <div class="media media-user offline" bis_skin_checked="1">
         <div class="media-left" bis_skin_checked="1">
            <div class="avatar-photo pseudo-a" tabindex="0" data-href="https://funpay.com/users/000000/" style="background-image: url(/img/layout/avatar.png);" bis_skin_checked="1"></div>
         </div>
         <div class="media-body" bis_skin_checked="1">
            <div class="media-user-name" bis_skin_checked="1">
               <span class="pseudo-a" tabindex="0" data-href="https://funpay.com/users/000000/">$username</span>
            </div>
            <div class="media-user-status" bis_skin_checked="1">был 1.000.000 лет назад</div>
         </div>
      </div>
   </div>
   <div class="tc-status text-primary" bis_skin_checked="1">Оплачен</div>
   <div class="tc-price text-nowrap tc-seller-sum" bis_skin_checked="1">999999.0 <span class="unit">₽</span></div>
</a>"""


# INIT MESSAGE
def save_init_chats_handler(c: Cardinal, e: InitialChatEvent):
    """
    Кэширует существующие чаты (чтобы не отправлять приветственные сообщения).
    """
    if c.MAIN_CFG["Greetings"].getboolean("sendGreetings") and e.chat.id not in c.old_users:
        c.old_users[e.chat.id] = int(time.time())
        cardinal_tools.cache_old_users(c.old_users)


def update_threshold_on_initial_chat(c: Cardinal, e: InitialChatEvent):
    """
    Обновляет пороговое значение для определения новых чатов.
    """
    if e.chat.id > c.greeting_chat_id_threshold:
        c.greeting_chat_id_threshold = e.chat.id


# NEW MESSAGE / LAST CHAT MESSAGE CHANGED
def old_log_msg_handler(c: Cardinal, e: LastChatMessageChangedEvent):
    """
    Логирует полученное сообщение.
    """
    if not c.old_mode_enabled:
        return
    text, chat_name, chat_id = str(e.chat), e.chat.name, e.chat.id
    username = c.account.username if not e.chat.unread else e.chat.name

    logger.info(_("log_new_msg", chat_name, chat_id))
    for index, line in enumerate(text.split("\n")):
        if not index:
            logger.info(f"$MAGENTA└───> $YELLOW{username}: $CYAN{line}")
        else:
            logger.info(f"      $CYAN{line}")


def log_msg_handler(c: Cardinal, e: NewMessageEvent):
    global MSG_LOG_LAST_STACK_ID
    if e.stack.id() == MSG_LOG_LAST_STACK_ID:
        return

    chat_name, chat_id = e.message.chat_name, e.message.chat_id

    logger.info(_("log_new_msg", chat_name, chat_id))
    for index, event in enumerate(e.stack.get_stack()):
        username, text = event.message.author, event.message.text or event.message.image_link
        for line_index, line in enumerate(text.split("\n")):
            if not index and not line_index:
                logger.info(f"$MAGENTA└───> $YELLOW{username}: $CYAN{line}")
            elif not line_index:
                logger.info(f"      $YELLOW{username}: $CYAN{line}")
            else:
                logger.info(f"      $CYAN{line}")
    MSG_LOG_LAST_STACK_ID = e.stack.id()


def update_threshold_on_last_message_change(c: Cardinal, e: LastChatMessageChangedEvent | NewMessageEvent):
    """
    Обновляет пороговое значение для определения новых чатов.
    """
    # Должно выполняться после greetings_handler для корректной обработки
    # c.greeting_threshold_chat_ids (чтобы не спамило приветствиями)
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return
        chat_id = e.message.chat_id
    else:
        chat_id = e.chat.id
    if e.runner_tag != c.last_greeting_chat_id_threshold_change_tag:
        c.greeting_chat_id_threshold = max([c.greeting_chat_id_threshold, *c.greeting_threshold_chat_ids])
        c.greeting_threshold_chat_ids = set()
        c.last_greeting_chat_id_threshold_change_tag = e.runner_tag
    c.greeting_threshold_chat_ids.add(chat_id)


def greetings_handler(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent):
    """
    Отправляет приветственное сообщение.
    """
    if not c.MAIN_CFG["Greetings"].getboolean("sendGreetings"):
        return
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return
        obj = e.message
        chat_id, chat_name, mtype, its_me, badge = obj.chat_id, obj.chat_name, obj.type, obj.author_id == c.account.id, obj.badge
    else:
        obj = e.chat
        chat_id, chat_name, mtype, its_me, badge = obj.id, obj.name, obj.last_message_type, not obj.unread, None
    is_old_chat = (chat_id <= c.greeting_chat_id_threshold or chat_id in c.greeting_threshold_chat_ids)

    if any([c.MAIN_CFG["Greetings"].getboolean("onlyNewChats") and is_old_chat,
            time.time() - c.old_users.get(chat_id, 0) < float(
                c.MAIN_CFG["Greetings"]["greetingsCooldown"]) * 24 * 60 * 60,
            its_me, mtype in (MessageTypes.DEAR_VENDORS, MessageTypes.ORDER_CONFIRMED_BY_ADMIN), badge is not None,
            (mtype is not MessageTypes.NON_SYSTEM and c.MAIN_CFG["Greetings"].getboolean("ignoreSystemMessages"))]):
        return

    logger.info(_("log_sending_greetings", chat_name, chat_id))
    text = cardinal_tools.format_msg_text(c.MAIN_CFG["Greetings"]["greetingsText"], obj)
    Thread(target=c.send_message, args=(chat_id, text, chat_name), daemon=True).start()


def add_old_user_handler(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent):
    """
    Добавляет пользователя в список написавших.
    """
    if not c.MAIN_CFG["Greetings"].getboolean("sendGreetings") or c.MAIN_CFG["Greetings"].getboolean("onlyNewChats"):
        return

    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return
        chat_id, mtype = e.message.chat_id, e.message.type
    else:
        chat_id, mtype = e.chat.id, e.chat.last_message_type

    if mtype == MessageTypes.DEAR_VENDORS:
        return

    c.old_users[chat_id] = int(time.time())
    cardinal_tools.cache_old_users(c.old_users)


def send_response_handler(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent):
    """
    Проверяет, является ли сообщение командой, и если да, отправляет ответ на данную команду.
    """
    if not c.autoresponse_enabled:
        return
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return
        obj, mtext = e.message, str(e.message)
        chat_id, chat_name, username = e.message.chat_id, e.message.chat_name, e.message.author
    else:
        obj, mtext = e.chat, str(e.chat)
        chat_id, chat_name, username = obj.id, obj.name, obj.name

    mtext = mtext.replace("\n", "")
    if any([c.bl_response_enabled and username in c.blacklist, (command := mtext.strip().lower()) not in c.AR_CFG]):
        return
    if not c.AR_CFG[command].getboolean("enabled"):
        return

    logger.info(_("log_new_cmd", command, chat_name, chat_id))
    response_text = cardinal_tools.format_msg_text(c.AR_CFG[command]["response"], obj)
    Thread(target=c.send_message, args=(chat_id, response_text, chat_name), daemon=True).start()


def old_send_new_msg_notification_handler(c: Cardinal, e: LastChatMessageChangedEvent):
    if any([not c.old_mode_enabled, not c.telegram, not e.chat.unread,
            c.bl_msg_notification_enabled and e.chat.name in c.blacklist,
            e.chat.last_message_type is not MessageTypes.NON_SYSTEM, str(e.chat).strip().lower() in c.AR_CFG.sections(),
            str(e.chat).startswith("!автовыдача")]):
        return
    user = e.chat.name
    if user in c.blacklist:
        user = f"🚷 {user}"
    elif e.chat.last_by_bot:
        user = f"🐦 {user}"
    else:
        user = f"👤 {user}"
    text = f"<i><b>{user}: </b></i><code>{utils.escape(str(e.chat))}</code>"
    kb = keyboards.reply(e.chat.id, e.chat.name, extend=True)
    Thread(target=c.telegram.send_notification, args=(text, kb, utils.NotificationTypes.new_message),
           daemon=True).start()


def send_new_msg_notification_handler(c: Cardinal, e: NewMessageEvent) -> None:
    """
    Отправляет уведомление о новом сообщении в телеграм.
    """
    global LAST_STACK_ID
    if not c.telegram or e.stack.id() == LAST_STACK_ID:
        return
    LAST_STACK_ID = e.stack.id()

    chat_id, chat_name = e.message.chat_id, e.message.chat_name
    if c.bl_msg_notification_enabled and chat_name in c.blacklist:
        return

    events = []
    nm, m, f, b = False, False, False, False
    for i in e.stack.get_stack():
        if i.message.author_id == 0:
            if c.include_fp_msg_enabled:
                events.append(i)
                f = True
        elif i.message.by_bot:
            if c.include_bot_msg_enabled:
                events.append(i)
                b = True
        elif i.message.author_id == c.account.id:
            if c.include_my_msg_enabled:
                events.append(i)
                m = True
        else:
            events.append(i)
            nm = True
    if not events:
        return

    if [m, f, b, nm].count(True) == 1 and \
            any([m and not c.only_my_msg_enabled, f and not c.only_fp_msg_enabled, b and not c.only_bot_msg_enabled]):
        return

    if len(events) < 2:
        message_text = str(e.message)
        if message_text.strip().lower() in c.AR_CFG.sections() or message_text.startswith("!автовыдача"):
            return

    text = utils.format_messages(c, [i.message for i in events])
    kb = keyboards.reply(chat_id, chat_name, extend=True)
    Thread(target=c.telegram.send_notification, args=(text, kb, utils.NotificationTypes.new_message),
           daemon=True).start()


def send_review_notification(c: Cardinal, order: Order, chat_id: int, reply_text: str | None):
    if not c.telegram:
        return
    reply_text = _("ntfc_review_reply_text").format(utils.escape(reply_text)) if reply_text else ""
    Thread(target=c.telegram.send_notification,
           args=(_("ntfc_new_review").format('⭐' * order.review.stars, order.id, utils.escape(order.review.text),
                                             reply_text),
                 keyboards.new_order(order.id, order.buyer_username, chat_id),
                 utils.NotificationTypes.review),
           daemon=True).start()


def process_review_handler(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent):
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return
        obj = e.message
        message_type, its_me = obj.type, obj.i_am_buyer
        message_text, chat_id = str(obj), obj.chat_id

    else:
        obj = e.chat
        message_type, its_me = obj.last_message_type, f" {c.account.username} " in str(obj)
        message_text, chat_id = str(obj), obj.id

    if message_type not in [types.MessageTypes.NEW_FEEDBACK, types.MessageTypes.FEEDBACK_CHANGED] or its_me:
        return

    def send_reply():
        try:
            order = c.get_order_from_object(obj)
            if order is None:
                raise Exception("Не удалось получить объект заказа.")  # locale
        except:
            logger.error(f"Не удалось получить информацию о заказе для сообщения: \"{message_text}\".")  # locale
            logger.debug("TRACEBACK", exc_info=True)
            return

        if not order.review or not order.review.stars:
            return

        logger.info(f"Изменен отзыв на заказ #{order.id}.")  # locale

        toggle = f"star{order.review.stars}Reply"
        text = f"star{order.review.stars}ReplyText"
        reply_text = None
        if c.MAIN_CFG["ReviewReply"].getboolean(toggle) and c.MAIN_CFG["ReviewReply"].get(text):
            try:
                # Укорачиваем текст до 999 символов (оставляем 1 на спецсимвол), до 10 строк
                def format_text4review(text_: str):
                    max_l = 999
                    text_ = text_[:max_l + 1]
                    if len(text_) > max_l:
                        ln = len(text_)
                        indexes = []
                        for char in (".", "!", "\n"):
                            index1 = text_.rfind(char)
                            indexes.extend([index1, text_[:index1].rfind(char)])
                        text_ = text_[:max(indexes, key=lambda x: (x < ln - 1, x))] + "🐦"
                    text_ = text_.strip()
                    while text_.count("\n") > 9 and text.count("\n\n") > 1:
                        # заменяем с конца все двойные переносы строк на одинарные, но оставляем как можно больше
                        # переносов строк и не менее одного двойного переноса
                        text_ = text_[::-1].replace("\n\n", "\n",
                                                    min([text_.count("\n\n") - 1, text_.count("\n") - 9]))[::-1]
                    if text_.count("\n") > 9:
                        text_ = text_[::-1].replace("\n", " ", text_.count("\n") - 9)[::-1]
                    return text_

                reply_text = cardinal_tools.format_order_text(c.MAIN_CFG["ReviewReply"].get(text), order)
                reply_text = format_text4review(reply_text)
                c.account.send_review(order.id, reply_text)
            except:
                logger.error(f"Произошла ошибка при ответе на отзыв {order.id}.")  # locale
                logger.debug("TRACEBACK", exc_info=True)
        send_review_notification(c, order, chat_id, reply_text)

        # ── Система поощрения: 5 звёзд → бонус добавляется сразу ────────
        if order.review.stars == 5:
            try:
                import random as _random
                from rental_system import get_rental_manager as _grm
                bonus_hours = _random.randint(1, 2)
                rm = _grm()
                # Ищем активную сессию покупателя и продлеваем её
                bonus_applied = False
                if rm:
                    session = rm.get_active_session_for_chat(chat_id)
                    if session and session.active and session.is_rental:
                        session.end_time += bonus_hours * 3600
                        session.duration_hours += bonus_hours
                        rm.save_sessions()
                        bonus_applied = True

                if bonus_applied:
                    bonus_msg = (
                        f"⭐ Спасибо за 5 звёзд!\n\n"
                        f"Бонус начислен прямо сейчас: +{bonus_hours} ч к текущей аренде.\n"
                        f"Приятной игры! 🎮"
                    )
                else:
                    bonus_msg = (
                        f"⭐ Спасибо за 5 звёзд!\n\n"
                        f"Ваш бонус: {bonus_hours} ч аренды — будет добавлен к следующей сессии.\n"
                        f"Ждём вас снова! 🎮"
                    )
                c.send_message(chat_id, bonus_msg, order.buyer_username, watermark=False)
                logger.info(f"Бонус за 5★: {order.buyer_username} — {bonus_hours} ч (applied={bonus_applied})")
            except Exception as _e:
                logger.error(f"Ошибка бонуса за отзыв: {_e}")

    Thread(target=send_reply, daemon=True).start()


def send_command_notification_handler(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent):
    """
    Отправляет уведомление о введенной команде в телеграм.
    """
    if not c.telegram:
        return
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return
        obj, message_text = e.message, str(e.message)
        chat_id, chat_name, username = e.message.chat_id, e.message.chat_name, e.message.author
    else:
        obj, message_text = e.chat, str(e.chat)
        chat_id, chat_name, username = obj.id, obj.name, obj.name if obj.unread else c.account.username

    if c.bl_cmd_notification_enabled and username in c.blacklist:
        return
    command = message_text.strip().lower()
    if (command not in c.AR_CFG or not c.AR_CFG[command].getboolean("telegramNotification")
            or not c.AR_CFG[command].getboolean("enabled")):
        return

    if not c.AR_CFG[command].get("notificationText"):
        text = f"🧑‍💻 Пользователь <b><i>{username}</i></b> ввел команду <code>{utils.escape(command)}</code>."  # locale
    else:
        text = cardinal_tools.format_msg_text(c.AR_CFG[command]["notificationText"], obj)

    Thread(target=c.telegram.send_notification, args=(text, keyboards.reply(chat_id, chat_name),
                                                      utils.NotificationTypes.command), daemon=True).start()


def test_auto_delivery_handler(c: Cardinal, e: NewMessageEvent | LastChatMessageChangedEvent):
    """
    Выполняет тест автовыдачи.
    """
    if not c.old_mode_enabled:
        if isinstance(e, LastChatMessageChangedEvent):
            return
        obj, message_text, chat_name, chat_id = e.message, str(e.message), e.message.chat_name, e.message.chat_id
    else:
        obj, message_text, chat_name, chat_id = e.chat, str(e.chat), e.chat.name, e.chat.id

    if not message_text.startswith("!автовыдача"):
        return

    split = message_text.split()
    if len(split) < 2:
        logger.warning("Одноразовый ключ автовыдачи не обнаружен.")  # locale
        return

    key = split[1].strip()
    if key not in c.delivery_tests:
        logger.warning("Невалидный одноразовый ключ автовыдачи.")  # locale
        return

    lot_name = c.delivery_tests[key]
    del c.delivery_tests[key]
    date = datetime.now()
    date_text = date.strftime("%H:%M")
    html = ORDER_HTML_TEMPLATE.replace("$username", chat_name).replace("$lot_name", lot_name).replace("$date",
                                                                                                      date_text)

    fake_order = OrderShortcut("ADTEST", lot_name, 0.0, Currency.UNKNOWN, chat_name, 000000, chat_id,
                               types.OrderStatuses.PAID,
                               date, "Авто-выдача, Тест", None, html)

    fake_event = NewOrderEvent(e.runner_tag, fake_order)
    c.run_handlers(c.new_order_handlers, (c, fake_event,))


def send_categories_raised_notification_handler(c: Cardinal, cat: types.Category, error_text: str = "",
                                                success: bool = True, wait_str: str = "") -> None:
    """
    Отправляет уведомление о поднятии лотов (или об ошибке таймера) в Telegram.
    """
    if not c.telegram:
        return

    if success:
        text = (f"⤴️ <b>Поднял лоты категории</b> <code>{cat.name}</code>\n"
                f"<tg-spoiler>{error_text}</tg-spoiler>")
    else:
        text = (f"⏳ <b>Не удалось поднять</b> <code>{cat.name}</code>\n"
                f"FunPay: <i>{error_text}</i>\n"
                f"Следующая попытка через <b>{wait_str}</b>")

    Thread(target=c.telegram.send_notification,
           args=(text,),
           kwargs={"notification_type": utils.NotificationTypes.lots_raise}, daemon=True).start()


# Изменен список ордеров (REGISTER_TO_ORDERS_LIST_CHANGED)
def get_lot_config_by_name(c: Cardinal, name: str) -> configparser.SectionProxy | None:
    """
    Ищет секцию лота в конфиге автовыдачи.

    :param c: объект кардинала.
    :param name: название лота.

    :return: секцию конфига или None.
    """
    for i in c.AD_CFG.sections():
        if i in name:
            return c.AD_CFG[i]
    return None


def check_products_amount(config_obj: configparser.SectionProxy) -> int:
    file_name = config_obj.get("productsFileName")
    if not file_name:
        return 1
    return cardinal_tools.count_products(f"storage/products/{file_name}")


# Новый ордер (REGISTER_TO_NEW_ORDER)
def log_new_order_handler(c: Cardinal, e: NewOrderEvent, *args):
    """
    Логирует новый заказ.
    """
    logger.info(f"Новый заказ! ID: $YELLOW#{e.order.id}$RESET")


def setup_event_attributes_handler(c: Cardinal, e: NewOrderEvent, *args):
    config_section_name = None
    config_section_obj = None
    lot_shortcut = None
    lot_id = None
    lot_description = e.order.description
    # пробуем найти лот, чтобы не выдавать по строке, которую вписал покупатель при оформлении заказа
    for lot in sorted(list(c.profile.get_sorted_lots(2).get(e.order.subcategory, {}).values()),
                      key=lambda l: len(f"{l.server}, {l.side}, {l.description}"), reverse=True):

        temp_desc = ", ".join([i for i in [lot.server, lot.side, lot.description] if i])

        if temp_desc in e.order.description:
            lot_description = temp_desc
            lot_shortcut = lot
            lot_id = lot.id
            break

    for i in range(3):
        # Собираем ВСЕ подходящие секции этого pass'а и берём самую длинную (специфичную), а не
        # первую по порядку в файле - иначе более общее название лота, добавленное в конфиг раньше,
        # могло бы перехватить заказ, который на самом деле соответствует более узкому названию.
        matched_lot_names = []
        for lot_name in c.AD_CFG:
            if i == 0:
                rule = lot_description == lot_name
            elif i == 1:
                rule = lot_description.startswith(lot_name)
            else:
                rule = lot_name in lot_description

            if rule:
                matched_lot_names.append(lot_name)

        if matched_lot_names:
            lot_name = max(matched_lot_names, key=len)
            config_section_obj = c.AD_CFG[lot_name]
            config_section_name = lot_name
            break

    attributes = {"config_section_name": config_section_name, "config_section_obj": config_section_obj,
                  "delivered": False, "delivery_text": None, "goods_delivered": 0, "goods_left": None,
                  "error": 0, "error_text": None, "lot_id": lot_id, "lot_shortcut": lot_shortcut}
    for i in attributes:
        setattr(e, i, attributes[i])

    if config_section_obj is None:
        logger.info("Лот не найден в конфиге авто-выдачи!")  # todo
    else:
        logger.info("Лот найден в конфиге авто-выдачи!")  # todo


def send_new_order_notification_handler(c: Cardinal, e: NewOrderEvent, *args):
    """
    Отправляет уведомления о новом заказе в телеграм.
    """
    if not c.telegram:
        return
    if e.order.buyer_username in c.blacklist and c.MAIN_CFG["BlockList"].getboolean("blockNewOrderNotification"):
        return
    if not (config_obj := getattr(e, "config_section_obj")):
        delivery_info = _("ntfc_new_order_not_in_cfg")
    else:
        if not c.autodelivery_enabled:
            delivery_info = _("ntfc_new_order_ad_disabled")
        elif config_obj.getboolean("disable"):
            delivery_info = _("ntfc_new_order_ad_disabled_for_lot")
        elif c.bl_delivery_enabled and e.order.buyer_username in c.blacklist:
            delivery_info = _("ntfc_new_order_user_blocked")
        else:
            delivery_info = _("ntfc_new_order_will_be_delivered")
    text = _("ntfc_new_order", f"{utils.escape(e.order.description)}, {utils.escape(e.order.subcategory_name)}",
             e.order.buyer_username, f"{e.order.price} {e.order.currency}", e.order.id, delivery_info)

    chat = c.account.get_chat_by_name(e.order.buyer_username)
    if chat:
        chat_id = chat.id
    else:
        chat_id = e.order.chat_id
    keyboard = keyboards.new_order(e.order.id, e.order.buyer_username, chat_id)
    Thread(target=c.telegram.send_notification, args=(text, keyboard, utils.NotificationTypes.new_order),
           daemon=True).start()


def deliver_goods(c: Cardinal, e: NewOrderEvent, *args):
    chat = c.account.get_chat_by_name(e.order.buyer_username)
    if chat:
        chat_id = chat.id
    else:
        chat_id = e.order.chat_id
    cfg_obj = getattr(e, "config_section_obj")
    delivery_text = cardinal_tools.format_order_text(cfg_obj["response"], e.order)

    amount, goods_left, products = 1, -1, []
    try:
        if file_name := cfg_obj.get("productsFileName"):
            if c.multidelivery_enabled and not cfg_obj.getboolean("disableMultiDelivery"):
                amount = e.order.amount if e.order.amount else 1
            products, goods_left = cardinal_tools.get_products(f"storage/products/{file_name}", amount)
            delivery_text = delivery_text.replace("$product", "\n".join(products).replace("\\n", "\n"))
    except Exception as exc:
        logger.error(
            f"Произошла ошибка при получении товаров для заказа $YELLOW{e.order.id}: {str(exc)}$RESET")  # locale
        logger.debug("TRACEBACK", exc)
        setattr(e, "error", 1)
        setattr(e, "error_text",
                f"Произошла ошибка при получении товаров для заказа {e.order.id}: {str(exc)}")  # locale
        return

    result = c.send_message(chat_id, delivery_text, e.order.buyer_username)
    if not result:
        logger.error(f"Не удалось отправить товар для ордера $YELLOW{e.order.id}$RESET.")  # locale
        setattr(e, "error", 1)
        setattr(e, "error_text", f"Не удалось отправить сообщение с товаром для заказа {e.order.id}.")  # locale
        if file_name and products:
            cardinal_tools.add_products(f"storage/products/{file_name}", products, at_zero_position=True)
    else:
        logger.info(f"Товар для заказа {e.order.id} выдан.")  # locale
        setattr(e, "delivered", True)
        setattr(e, "delivery_text", delivery_text)
        setattr(e, "goods_delivered", amount)
        setattr(e, "goods_left", goods_left)
        setattr(e, "delivered_products", products)


def deliver_product_handler(c: Cardinal, e: NewOrderEvent, *args) -> None:
    """
    Обертка для deliver_product(), обрабатывающая ошибки.
    """
    if not c.MAIN_CFG["FunPay"].getboolean("autoDelivery"):
        return
    if e.order.buyer_username in c.blacklist and c.bl_delivery_enabled:
        logger.info(f"Пользователь {e.order.buyer_username} находится в ЧС и включена блокировка автовыдачи. "
                    f"$YELLOW(ID: {e.order.id})$RESET")  # locale
        return

    if (config_section_obj := getattr(e, "config_section_obj")) is None:
        return
    if config_section_obj.getboolean("disable"):
        logger.info(f"Для лота \"{e.order.description}\" отключена автовыдача.")  # locale
        return

    c.run_handlers(c.pre_delivery_handlers, (c, e))

    deliver_goods(c, e, *args)

    # Регистрируем покупку для доступа к Guard кодам
    purchase_checker_instance = purchase_checker.get_purchase_checker()
    if purchase_checker_instance:
        purchase_checker_instance.add_purchase(e.order.chat_id, e.order.buyer_username)
        logger.info(f"Покупка зарегистрирована для Guard кодов: {e.order.buyer_username} (chat_id: {e.order.chat_id})")
        # Привязка чата к Steam-аккаунту (для корректного !code при нескольких аккаунтах)
        delivered_lines = getattr(e, "delivered_products", []) or []
        if delivered_lines:
            acct = _parse_steam_login(delivered_lines[0])
            if acct:
                purchase_checker_instance.set_account_for_chat(e.order.chat_id, acct)
                logger.info(f"Чат {e.order.chat_id} привязан к Steam-аккаунту {acct}")

    # Привязка чата к заказу (для автовозврата)
    try:
        from refund_handler import register_order_for_chat
        register_order_for_chat(e.order.chat_id, e.order.id)
    except Exception:
        pass

    # Создаём сессию (аренда или оффлайн активация) при наличии ключевых слов в названии лота
    rental_manager = rental_system.get_rental_manager()
    if rental_manager and c.MAIN_CFG.getboolean("Rental", "enabled", fallback=False):
        lot_lower = e.order.description.lower()
        is_rental  = "аренд" in lot_lower or "rental" in lot_lower or "steam online" in lot_lower
        is_offline = "offline" in lot_lower or "оффлайн" in lot_lower or "steam offline" in lot_lower

        if is_rental or is_offline:
            try:
                from rental_system import RentalSession as RS
                if is_offline and is_rental:
                    logger.warning(f"Лот '{e.order.description}' содержит и 'аренда' и 'offline' — "
                                   f"используется тип offline")
                session_type = RS.SESSION_TYPE_OFFLINE if is_offline else RS.SESSION_TYPE_RENTAL
                # Для оффлайн — 2 часа, для аренды — из конфига
                if is_offline:
                    default_hours = 2
                else:
                    default_hours = c.MAIN_CFG.getint("Rental", "default_rental_hours", fallback=24)

                # Извлекаем название игры из описания
                # Формат лота: "RUST (STEAM OFFLINE)" → "RUST"
                import re as _re
                game_name = _re.sub(
                    r'\(?\s*(STEAM\s+OFFLINE|STEAM\s+ONLINE|STEAM\s+OFFLINE\/ONLINE|аренда|rental|оффлайн|offline)\s*\)?',
                    '', e.order.description, flags=_re.IGNORECASE).strip().strip("()").strip()
                if not game_name:
                    game_name = e.order.description

                account_name = c.account.username  # индекс account_sessions
                delivered_lines = getattr(e, "delivered_products", []) or []
                steam_login = steam_password = steamid = None
                if delivered_lines:
                    parts_prod = delivered_lines[0].split(":")
                    if len(parts_prod) >= 2:
                        steam_login, steam_password = parts_prod[0], parts_prod[1]
                        if len(parts_prod) >= 3 and parts_prod[2].isdigit() and len(parts_prod[2]) >= 17:
                            steamid = parts_prod[2]

                profile_baseline = None
                try:
                    from profile_guardian import get_profile_guardian
                    pg = get_profile_guardian()
                    if pg and steamid:
                        profile_baseline = pg.capture_baseline(steamid)
                except Exception:
                    pass

                session = rental_manager.create_session(
                    user_id=str(e.order.chat_id),
                    game_name=game_name,
                    account_name=account_name,
                    duration_hours=default_hours,
                    steam_login=steam_login,
                    steam_password=steam_password,
                    steamid=steamid,
                    profile_baseline=profile_baseline,
                    session_type=session_type,
                )
                if steam_login and steam_password:
                    rental_manager.store_credential(session.session_id, steam_login, steam_password)

                logger.info(f"Создана сессия [{session_type}]: {game_name} "
                            f"({default_hours} ч) для {e.order.buyer_username}")

                # ── Сопроводительные письма покупателю (несколько сообщений) ──
                def _send_letters(chat_id, chat_name, gname, dtype, hours):
                    import time as _t
                    if dtype == RS.SESSION_TYPE_OFFLINE:
                        msgs = [
                            # Письмо 1 — приветствие
                            (
                                f"Спасибо за покупку! 🎮\n\n"
                                f"Товар: <b>{gname}</b> (Steam Offline)\n"
                                f"Данные аккаунта — выше. Приятной игры!"
                            ),
                            # Письмо 2 — пошаговая инструкция
                            (
                                f"<b>Инструкция по активации:</b>\n\n"
                                f"1. Введите логин и пароль из сообщения выше в Steam\n\n"
                                f"2. Появится окно запроса кода — вернитесь в этот чат и напишите:\n"
                                f"   <code>!code</code>\n"
                                f"   ⏱ Код действует 30 сек. Введите его в Steam\n\n"
                                f"3. В библиотеке найдите <b>{gname}</b> → установите\n\n"
                                f"4. Запустите игру, дождитесь главного меню\n\n"
                                f"5. В главном меню нажмите «Выход» (корректный выход из игры)\n\n"
                                f"6. Перейдите в Steam → меню → «Перейти в автономный режим»\n\n"
                                f"7. Напишите <code>!offline_done</code> — активация подтверждена\n\n"
                                f"8. Подтвердите заказ на FunPay ✅\n\n"
                                f"⚠️ На весь процесс — <b>2 часа</b> с момента первого <code>!code</code>"
                            ),
                            # Письмо 3 — правила + благодарность
                            (
                                f"<b>Правила:</b>\n\n"
                                f"🔒 Не менять в аккаунте ничего: пароль, почту, имя, аватар, настройки — нарушение ведёт к блокировке без возврата\n\n"
                                f"🚫 Не скачивать игры, которые не приобретены\n\n"
                                f"🚫 Перепродажа данных аккаунта запрещена\n\n"
                                f"💳 Возврат после первого <code>!code</code> невозможен\n\n"
                                f"⭐ Оставьте 5 звёзд — получите +1–2 ч аренды в подарок сразу\n\n"
                                f"Благодарим за соблюдение условий. Ждём вас снова! 🤝"
                            ),
                        ]
                    else:
                        msgs = [
                            # Письмо 1 — приветствие
                            (
                                f"Спасибо за покупку! 🎮\n\n"
                                f"Товар: <b>{gname}</b> (Аренда {hours} ч)\n"
                                f"Данные аккаунта — выше. Приятной игры!"
                            ),
                            # Письмо 2 — пошаговая инструкция
                            (
                                f"<b>Инструкция по аренде:</b>\n\n"
                                f"1. Введите логин и пароль из сообщения выше в Steam\n\n"
                                f"2. Появится окно кода — вернитесь в этот чат и напишите:\n"
                                f"   <code>!code</code>\n"
                                f"   ⏱ Код действует 30 сек. Введите его в Steam\n\n"
                                f"⚠️ С момента первого <code>!code</code> у вас <b>1.5 часа</b> на скачивание игры\n\n"
                                f"3. В чате выберите игру:\n"
                                f"   <code>!select_game {gname}</code>\n\n"
                                f"4. Скачайте игру, затем напишите:\n"
                                f"   <code>!downloaded</code>\n"
                                f"   ⏱ С этого момента идёт отсчёт <b>{hours} ч</b> аренды\n\n"
                                f"5. Проверить остаток: <code>!rental_status</code>\n\n"
                                f"6. Продлить (N = часы): <code>!extend N</code>\n\n"
                                f"По истечении времени <code>!code</code> станет недоступен"
                            ),
                            # Письмо 3 — правила + благодарность
                            (
                                f"<b>Правила аренды:</b>\n\n"
                                f"🔒 Не менять в аккаунте ничего — нарушение ведёт к закрытию сессии без возврата\n\n"
                                f"🚫 Не скачивать игры, которые не приобретены\n\n"
                                f"🚫 Перепродажа данных запрещена\n\n"
                                f"💳 Возврат после входа в аккаунт невозможен\n\n"
                                f"⭐ Оставьте 5 звёзд — получите +1–2 ч к аренде прямо сейчас\n\n"
                                f"Благодарим за соблюдение условий. Ждём вас снова! 🤝"
                            ),
                        ]

                    for msg in msgs:
                        try:
                            c.send_message(chat_id, msg, chat_name, watermark=False)
                        except Exception:
                            pass
                        _t.sleep(0.8)

                Thread(
                    target=_send_letters,
                    args=(e.order.chat_id, e.order.chat_name, game_name, session_type, default_hours),
                    daemon=True,
                ).start()

                # ── Уведомление владельца в Telegram ──────────────────────
                if c.telegram and c.telegram.bot:
                    tg_note = (
                        f"🆕 Новая сессия [{session_type.upper()}]\n\n"
                        f"👤 {e.order.buyer_username} (заказ #{e.order.id})\n"
                        f"🎮 {game_name} | ⏱ {default_hours} ч\n"
                        f"🔑 Steam: {steam_login or '—'}\n"
                        f"🆔 Сессия: <code>{session.session_id}</code>"
                    )
                    try:
                        owner_id = getattr(c.telegram, "owner_chat_id", None)
                        if owner_id:
                            Thread(target=c.telegram.bot.send_message,
                                   args=(owner_id, tg_note),
                                   daemon=True).start()
                    except Exception:
                        pass

            except Exception as ex:
                logger.error(f"Ошибка создания сессии: {ex}", exc_info=True)

    c.run_handlers(c.post_delivery_handlers, (c, e))


# REGISTER_TO_POST_DELIVERY
def send_delivery_notification_handler(c: Cardinal, e: NewOrderEvent):
    """
    Отправляет уведомление в телеграм об отправке товара.
    """
    if c.telegram is None:
        return

    if getattr(e, "error"):
        text = f"""❌ <code>{getattr(e, "error_text")}</code>"""
    else:
        amount = "<b>∞</b>" if getattr(e, "goods_left") == -1 else f"<code>{getattr(e, 'goods_left')}</code>"
        text = f"""✅ Успешно выдал товар для ордера <code>{e.order.id}</code>.\n
🛒 <b><i>Товар:</i></b>
<code>{utils.escape(getattr(e, "delivery_text"))}</code>\n
📋 <b><i>Осталось товаров: </i></b>{amount}"""  # locale

    Thread(target=c.telegram.send_notification, args=(text,),
           kwargs={"notification_type": utils.NotificationTypes.delivery}, daemon=True).start()

def update_current_lots(c: Cardinal, e: NewOrderEvent):
    logger.info("Получаю информацию о лотах...")  # locale
    attempts = 3
    while attempts:
        try:
            c.curr_profile = c.account.get_user(c.account.id)
            c.curr_profile_last_tag = e.runner_tag
            break
        except:
            logger.error("Произошла ошибка при получении информации о лотах.")  # locale
            logger.debug("TRACEBACK", exc_info=True)
            attempts -= 1
            time.sleep(2)
    else:
        logger.error("Не удалось получить информацию о лотах: превышено кол-во попыток.")  # locale
        return


def update_profile_lots(c: Cardinal, e: NewOrderEvent):
    """Обновляет лоты в c.profile"""
    if c.curr_profile_last_tag != e.runner_tag or c.profile_last_tag == e.runner_tag:
        return
    c.profile_last_tag = e.runner_tag
    lots = c.curr_profile.get_sorted_lots(1)

    for lot_id, lot in lots.items():
        c.profile.update_lot(lot)

def update_lot_state(cardinal: Cardinal, lot: types.LotShortcut, task: int) -> bool:
    """
    Обновляет состояние лота

    :param cardinal: объект Кардинала.
    :param lot: объект лота.
    :param task: -1 - деактивировать лот. 1 - активировать лот.

    :return: результат выполнения.
    """
    attempts = 3
    while attempts:
        try:
            lot_fields = cardinal.account.get_lot_fields(lot.id)
            if task == (1 if lot_fields.active else -1):
                # если лот и так в нужном состоянии
                return True
            elif task == 1:
                lot_fields.active = True
                cardinal.account.save_lot(lot_fields)
                logger.info(f"Восстановил лот $YELLOW{lot.id} - {lot.description}$RESET.")  # locale
            elif task == -1:
                lot_fields.active = False
                cardinal.account.save_lot(lot_fields)
                logger.info(f"Деактивировал лот $YELLOW{lot.id} - {lot.description}$RESET.")  # locale
            return True
        except Exception as e:
            if isinstance(e, exceptions.LotParsingError):
                logger.error(f"Произошла ошибка при изменении состояния лота $YELLOW{lot.description}$RESET:"  # locale
                             "лот не найден.")
                return False
            logger.error(f"Произошла ошибка при изменении состояния лота $YELLOW{lot.description}$RESET.")  # locale
            logger.debug("TRACEBACK", exc_info=True)
            attempts -= 1
            time.sleep(2)
    logger.error(
        f"Не удалось изменить состояние лота $YELLOW{lot.description}$RESET: превышено кол-во попыток.")  # locale
    return False


def update_lots_states(cardinal: Cardinal, event: NewOrderEvent):
    if not any([cardinal.autorestore_enabled, cardinal.autodisable_enabled]):
        return
    curr_profile_tag = cardinal.curr_profile_last_tag
    if cardinal.last_state_change_tag == curr_profile_tag:
        return
    cardinal.last_state_change_tag = curr_profile_tag
    lots = cardinal.curr_profile.get_sorted_lots(1)

    deactivated = []
    restored = []
    for lot in cardinal.profile.get_sorted_lots(3)[SubCategoryTypes.COMMON].values():
        if not lot.description:
            continue
        # -1 - деактивировать
        # 0 - ничего не делать
        # 1 - восстановить
        current_task = 0
        config_obj = get_lot_config_by_name(cardinal, lot.description)

        # Если лот уже деактивирован
        if lot.id not in lots:
            # и не найден в конфиге автовыдачи (глобальное автовосстановление включено)
            if config_obj is None:
                if cardinal.autorestore_enabled:
                    current_task = 1

            # и найден в конфиге автовыдачи
            else:
                # и глобальное автовосстановление вкл. + не выключено в самом лоте в конфиге автовыдачи
                if cardinal.autorestore_enabled and config_obj.get("disableAutoRestore") in ["0", None]:
                    # если глобальная автодеактивация выключена - восстанавливаем.
                    if not cardinal.autodisable_enabled:
                        current_task = 1
                    # если глобальная автодеактивация включена - восстанавливаем только если есть товары.
                    else:
                        if check_products_amount(config_obj):
                            current_task = 1

        # Если же лот активен
        else:
            # и найден в конфиге автовыдачи
            if config_obj:
                products_count = check_products_amount(config_obj)
                # и все условия выполнены: нет товаров + включено глобальная автодеактивация + она не выключена в
                # самом лоте в конфига автовыдачи - отключаем.
                if all((not products_count, cardinal.MAIN_CFG["FunPay"].getboolean("autoDisable"),
                        config_obj.get("disableAutoDisable") in ["0", None])):
                    current_task = -1

        if current_task:
            result = update_lot_state(cardinal, lot, current_task)
            if result:
                if current_task == -1:
                    deactivated.append(lot.description)
                elif current_task == 1:
                    restored.append(lot.description)
            time.sleep(0.5)

    if deactivated:
        lots = "\n".join(deactivated)  # locale
        text = f"""🔴 <b>Деактивировал лоты:</b>
        
<code>{lots}</code>"""
        Thread(target=cardinal.telegram.send_notification, args=(text,),
               kwargs={"notification_type": utils.NotificationTypes.lots_deactivate}, daemon=True).start()
    if restored:
        lots = "\n".join(restored)  # locale
        text = f"""🟢 <b>Активировал лоты:</b>

<code>{lots}</code>"""
        Thread(target=cardinal.telegram.send_notification, args=(text,),
               kwargs={"notification_type": utils.NotificationTypes.lots_restore}, daemon=True).start()


def update_profiles_handler(cardinal: Cardinal, event: NewOrderEvent | OrdersListChangedEvent, *args):
    """Обновляет информацию о профилях и состояния лотов в отдельном потоке."""
    def f(c: Cardinal, e: NewOrderEvent):
        try:
            update_current_lots(c, e)
            update_profile_lots(c, e)
            update_lots_states(c, e)
        except:
            logger.warning("Произошла ошибка при обновлении информации о профилях и состояний лотов.")
            logger.debug("TRACEBACK", exc_info=True)

    if event.runner_tag != cardinal.last_profile_refresh_event_tag:
        cardinal.last_profile_refresh_event_tag = event.runner_tag
        Thread(target=f, args=(cardinal, event), daemon=True).start()

# BIND_TO_ORDER_STATUS_CHANGED
def send_thank_u_message_handler(cardinal: Cardinal, event: OrderStatusChangedEvent):
    """
    Отправляет ответное сообщение на подтверждение заказа.
    """
    if not cardinal.MAIN_CFG["OrderConfirm"].getboolean("sendReply") or event.order.status is not types.OrderStatuses.CLOSED:
        return

    text = cardinal_tools.format_order_text(cardinal.MAIN_CFG["OrderConfirm"]["replyText"], event.order)
    chat = cardinal.account.get_chat_by_name(event.order.buyer_username)
    if chat:
        chat_id = chat.id
    else:
        chat_id = event.order.chat_id
    logger.info(f"Пользователь $YELLOW{event.order.buyer_username}$RESET подтвердил выполнение заказа "  # locale
                f"$YELLOW{event.order.id}.$RESET")  # locale
    logger.info(f"Отправляю ответное сообщение ...")  # locale
    Thread(target=cardinal.send_message, args=(chat_id, text, event.order.buyer_username),
           kwargs={'watermark': cardinal.MAIN_CFG["OrderConfirm"].getboolean("watermark")}, daemon=True).start()


def send_order_confirmed_notification_handler(cardinal: Cardinal, event: OrderStatusChangedEvent):
    """
    Отправляет уведомление о подтверждении заказа в Telegram.
    """
    if not event.order.status == types.OrderStatuses.CLOSED:
        return

    chat = cardinal.account.get_chat_by_name(event.order.buyer_username)
    if chat:
        chat_id = chat.id
    else:
        chat_id = event.order.chat_id
    Thread(target=cardinal.telegram.send_notification,  # locale
           args=(
               f"""🪙 Пользователь <a href="https://funpay.com/chat/?node={chat_id}">{event.order.buyer_username}</a> """
               f"""подтвердил выполнение заказа <code>{event.order.id}</code>. (<code>{event.order.price} {event.order.currency}</code>)""",
               keyboards.new_order(event.order.id, event.order.buyer_username, chat_id),
               utils.NotificationTypes.order_confirmed),
           daemon=True).start()


def send_bot_started_notification_handler(c: Cardinal, *args):
    """
    Отправляет уведомление о запуске бота в телеграм.
    """
    if c.telegram is None:
        return
    text = _("fpc_init", c.VERSION, c.account.username, c.account.id,
             c.balance.total_rub, c.balance.total_usd, c.balance.total_eur, c.account.active_sales)
    for i in c.telegram.init_messages:
        try:
            c.telegram.bot.edit_message_text(text, i[0], i[1])
        except:
            continue


BIND_TO_INIT_MESSAGE = [save_init_chats_handler, update_threshold_on_initial_chat]

BIND_TO_LAST_CHAT_MESSAGE_CHANGED = [old_log_msg_handler,
                                     greetings_handler,
                                     update_threshold_on_last_message_change,
                                     add_old_user_handler,
                                     send_response_handler,
                                     process_review_handler,
                                     old_send_new_msg_notification_handler,
                                     send_command_notification_handler,
                                     test_auto_delivery_handler]

BIND_TO_NEW_MESSAGE = [log_msg_handler,
                       greetings_handler,
                       update_threshold_on_last_message_change,
                       add_old_user_handler,
                       send_response_handler,
                       process_review_handler,
                       send_new_msg_notification_handler,
                       send_command_notification_handler,
                       test_auto_delivery_handler]

BIND_TO_POST_LOTS_RAISE = [send_categories_raised_notification_handler]

# BIND_TO_ORDERS_LIST_CHANGED = [update_profiles_handler]

def _resolve_primary_account(ss_cfg) -> str | None:
    """
    Возвращает имя секции основного Guard-аккаунта в shared_secrets.cfg:
    1) секция с 'primary = true';
    2) секция, чей steam_id совпадает с [Steam] steam_id из _main.cfg;
    3) единственная секция;
    4) первая секция.
    """
    import configparser
    import os as _os
    try:
        sections = ss_cfg.sections()
        if not sections:
            return None
        for sec in sections:
            if ss_cfg[sec].get('primary', '').strip().lower() in ('1', 'true', 'yes', 'on'):
                return sec
        try:
            mc = configparser.ConfigParser()
            main_path = _os.path.join('configs', '_main.cfg')
            if _os.path.exists(main_path):
                mc.read(main_path, encoding='utf-8')
                target = mc.get('Steam', 'steam_id', fallback='').strip()
                if target:
                    for sec in sections:
                        if ss_cfg[sec].get('steam_id', '').strip() == target:
                            return sec
        except Exception:
            pass
        if len(sections) == 1:
            return sections[0]
        return sections[0]
    except Exception:
        return None


def register_purchase_on_order_handler(c: Cardinal, e: NewOrderEvent, *args):
    """
    Регистрирует покупку в purchase_checker при ЛЮБОМ новом заказе.
    Это нужно чтобы !code работал даже если лот не настроен в auto_delivery.cfg.
    Привязка к Steam-аккаунту делается по имени лота: ищем shared_secret с совпадающим steam_login.
    """
    try:
        pc = purchase_checker.get_purchase_checker()
        if not pc:
            return
        pc.add_purchase(e.order.chat_id, e.order.buyer_username)

        # Пытаемся определить Steam-аккаунт по лоту — ищем в shared_secrets.cfg
        if not pc.get_account_for_chat(e.order.chat_id):
            import configparser as _cp
            _ss = _cp.ConfigParser()
            _ss.read("configs/shared_secrets.cfg", encoding="utf-8")
            # Если только один аккаунт — привязываем его сразу
            sections = _ss.sections()
            if len(sections) == 1:
                pc.set_account_for_chat(e.order.chat_id, sections[0])
                logger.info(f"Авто-привязка {e.order.buyer_username} → {sections[0]} (единственный аккаунт)")
            elif len(sections) > 1:
                # Ищем совпадение имени аккаунта в описании лота
                lot_lower = e.order.description.lower()
                bound = None
                for sec in sections:
                    uname = _ss[sec].get("username", sec).lower()
                    if uname in lot_lower:
                        bound = sec
                        break
                # Если имя не найдено в лоте — привязываем к основному Guard-аккаунту
                # (primary=true или совпадающий по steam_id из _main.cfg), чтобы !code
                # не выдал код от первого попавшегося аккаунта.
                if not bound:
                    bound = _resolve_primary_account(_ss)
                if bound:
                    pc.set_account_for_chat(e.order.chat_id, bound)
                    logger.info(f"Привязка {e.order.buyer_username} → {bound} по основному аккаунту")
    except Exception as _ex:
        logger.error(f"register_purchase_on_order_handler: {_ex}")


BIND_TO_NEW_ORDER = [log_new_order_handler, setup_event_attributes_handler,
                     send_new_order_notification_handler,
                     register_purchase_on_order_handler,
                     deliver_product_handler,
                     update_profiles_handler]

BIND_TO_ORDER_STATUS_CHANGED = [send_thank_u_message_handler, send_order_confirmed_notification_handler]

BIND_TO_POST_DELIVERY = [send_delivery_notification_handler]

BIND_TO_POST_START = [send_bot_started_notification_handler]
