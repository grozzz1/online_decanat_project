from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from utils.callbacks import reply_data, web_reply_data


DEAN_QUESTION = "❔ Задать вопрос деканату"
SEND_APPLICATION = "📝 Отправить заявление"
APPLICATION_QUESTION = "🔍 Задать вопрос по заявлению"
BACK = "⬅ Назад в меню"
APPLICATIONS = ("Заявление на академический отпуск",)
APPLICATION_QUESTIONS = (
    "Справка об обучении",
    "Заявление на академический отпуск",
    "Справка о практике",
)

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
    markup.add(InlineKeyboardButton(
        "✉ Ответить студенту",
        callback_data=reply_data(token, message.chat.id, message.message_id),
    ))
    return markup


def cancel_reply_menu(user_id):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Отменить", callback_data=f"cancel_reply:{user_id}"))
    return markup


def answer_web_student_menu(token, request_id):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(
        "✉ Ответить студенту",
        callback_data=web_reply_data(token, request_id),
    ))
    return markup
