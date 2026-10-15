from telebot.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from utils.callbacks import reply_data, web_reply_data
from utils.config import get_public_base_url
from utils.document_fields import DOCUMENT_SCHEMAS

DEAN_QUESTION = "❔ Задать вопрос деканату"
SEND_APPLICATION = "📝 Отправить заявление"
APPLICATION_QUESTION = "🔍 Задать вопрос по заявлению"
BACK = "⬅ Назад в меню"
APPLICATIONS = tuple(schema["label"] for schema in DOCUMENT_SCHEMAS.values())
APPLICATION_QUESTIONS = APPLICATIONS


def available_document_types(bot, mode):
    store = getattr(getattr(bot, "web", None), "store", None)
    if store is not None:
        return store.types.list(mode)
    return [
        {"key": key, "label": schema["label"], "custom": False} for key, schema in DOCUMENT_SCHEMAS.items()
    ]


def document_menu(bot, mode):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, input_field_placeholder="Выбери тип документа")
    for item in available_document_types(bot, mode):
        markup.add(KeyboardButton(item["label"]))
    markup.add(KeyboardButton(BACK))
    return markup


def miniapp_menu():
    url = get_public_base_url()
    if not url.startswith("https://"):
        return None
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Открыть личный кабинет", web_app=WebAppInfo(url + "/miniapp")))
    return markup


main_menu = ReplyKeyboardMarkup(
    resize_keyboard=True,
    input_field_placeholder="📌 Выбери пункт меню",
)
main_menu.add(KeyboardButton(DEAN_QUESTION))
main_menu.add(KeyboardButton(SEND_APPLICATION))
main_menu.add(KeyboardButton(APPLICATION_QUESTION))

applications_menu = ReplyKeyboardMarkup(
    resize_keyboard=True,
    input_field_placeholder="📌 Выбери заявление",
)
for name in APPLICATIONS:
    applications_menu.add(KeyboardButton(name))
applications_menu.add(KeyboardButton(BACK))

application_questions_menu = ReplyKeyboardMarkup(
    resize_keyboard=True,
    input_field_placeholder="📌 Выбери документ",
)
for name in APPLICATION_QUESTIONS:
    application_questions_menu.add(KeyboardButton(name))
application_questions_menu.add(KeyboardButton(BACK))

back_menu = ReplyKeyboardMarkup(
    resize_keyboard=True,
    input_field_placeholder="Отправь сообщение или вернись в меню",
)
back_menu.add(KeyboardButton(BACK))


def answer_student_menu(token, message):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(
            "✉ Ответить студенту",
            callback_data=reply_data(token, message.chat.id, message.message_id),
        )
    )
    return markup


def cancel_reply_menu(user_id):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Отменить", callback_data=f"cancel_reply:{user_id}"))
    return markup


def answer_web_student_menu(token, request_id):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(
            "✉ Ответить студенту",
            callback_data=web_reply_data(token, request_id),
        )
    )
    return markup
