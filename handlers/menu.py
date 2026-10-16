from html import escape
from urllib.parse import parse_qs, urlsplit

from utils.keyboards import (
    APPLICATION_QUESTION,
    BACK,
    DEAN_QUESTION,
    SEND_APPLICATION,
    available_document_types,
    back_menu,
    document_menu,
    main_menu,
    miniapp_menu,
)
from utils.states import application_types, clear_state, states
from utils.validation import is_text


async def handle_menu(bot, message):
    if not is_text(message):
        return False

    key = (message.chat.id, message.from_user.id)
    text = message.text
    command = text.split(maxsplit=1)[0].split("@", 1)[0]

    if command in ("/start", "/cancel") or text == BACK:
        clear_state(key)
        text = "Выбери, с чем нужна помощь:"

        if command == "/start" and getattr(bot, "web", None) is not None:
            link = await bot.web.create_login(message.from_user)
            code = parse_qs(urlsplit(link).query)["code"][0]
            text += f"\n\nМожно обратиться и через сайт:\n{escape(link)}"
            text += f"\n\nКод для входа (нажми, чтобы скопировать):\n<code>{escape(code)}</code>"

        await bot.send_message(
            message.chat.id,
            text,
            reply_markup=main_menu,
            disable_web_page_preview=True,
            parse_mode="HTML",
        )
        if command == "/start" and (markup := miniapp_menu()) is not None:
            await bot.send_message(
                message.chat.id, "Личный кабинет доступен прямо в Telegram:", reply_markup=markup
            )
    elif text == DEAN_QUESTION:
        clear_state(key)
        states[key] = "dean_question"
        await bot.send_message(
            message.chat.id,
            "Напиши свой вопрос деканату:",
            reply_markup=back_menu,
        )
    elif text == SEND_APPLICATION:
        clear_state(key)
        states[key] = "application_type"
        await bot.send_message(
            message.chat.id,
            "Выбери тип документа:",
            reply_markup=document_menu(bot, "application"),
        )
    elif text == APPLICATION_QUESTION:
        clear_state(key)
        states[key] = "application_question_type"
        await bot.send_message(
            message.chat.id,
            "Выбери документ, по которому хочешь задать вопрос:",
            reply_markup=document_menu(bot, "application_question"),
        )
    elif states.get(key) == "application_type" and text in [
        item["label"] for item in available_document_types(bot, "application")
    ]:
        application_types[key] = text
        states[key] = "application_photo"
        await bot.send_message(
            message.chat.id,
            f"{text}. Пришли фото документа или файл JPG, PNG, PDF до 10 МБ:",
            reply_markup=back_menu,
        )
    elif states.get(key) == "application_question_type" and text in [
        item["label"] for item in available_document_types(bot, "application_question")
    ]:
        application_types[key] = text
        states[key] = "application_question"
        await bot.send_message(
            message.chat.id,
            f"{text}. Пришли фото или файл JPG, PNG, PDF до 10 МБ и напиши вопрос в подписи:",
            reply_markup=back_menu,
        )
    else:
        return False

    return True
