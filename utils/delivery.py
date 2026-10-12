from telebot import util
from utils.users import get_username


async def send_question(bot, dean_chat_id, user, question, markup, source=""):
    text = f"Получен вопрос от пользователя {get_username(user)}:\n\n{question.strip()}"

    if source:
        text += f"\n\nИсточник: {source}"

    parts = util.split_string(text, chars_per_string=4096)
    first = await bot.send_message(dean_chat_id, parts[0], reply_markup=markup)

    for part in parts[1:]:
        await bot.send_message(dean_chat_id, part, reply_to_message_id=first.message_id)


async def send_application(bot, dean_chat_id, user, photo, application_type, recognized, question=None, markup=None, source=""):
    text = (
        f"Получено заявление от пользователя {get_username(user)}\n"
        f"Тип: {application_type}"
    )

    if source:
        text += f"\nИсточник: {source}"

    if question is not None:
        text += f"\n\nВопрос: {question.strip()}"

    text += f"\n\nТекст с фото:\n{recognized or 'Не удалось распознать текст.'}"
    photo.seek(0)
    sent = await bot.send_photo(dean_chat_id, photo, caption=text[:1024], reply_markup=markup)

    for part in util.split_string(text[1024:], chars_per_string=4096):
        await bot.send_message(dean_chat_id, part, reply_to_message_id=sent.message_id)
