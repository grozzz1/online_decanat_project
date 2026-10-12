from utils.keyboards import (
    APPLICATIONS,
    APPLICATION_QUESTION,
    APPLICATION_QUESTIONS,
    BACK,
    DEAN_QUESTION,
    SEND_APPLICATION,
    application_questions_menu,
    applications_menu,
    back_menu,
    main_menu,
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
            text += f"\n\nМожно обратиться и через сайт:\n{link}"

        await bot.send_message(
            message.chat.id,
            text,
            reply_markup=main_menu,
            disable_web_page_preview=True,
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
            "Выбери заявление:",
            reply_markup=applications_menu,
        )
    elif text == APPLICATION_QUESTION:
        clear_state(key)
        states[key] = "application_question_type"
        await bot.send_message(
            message.chat.id,
            "Выбери документ, по которому хочешь задать вопрос:",
            reply_markup=application_questions_menu,
        )
    elif states.get(key) == "application_type" and text in APPLICATIONS:
        application_types[key] = text
        states[key] = "application_photo"
        await bot.send_message(
            message.chat.id,
            f"{text}. Пришли одно фото заявления:",
            reply_markup=back_menu,
        )
    elif states.get(key) == "application_question_type" and text in APPLICATION_QUESTIONS:
        application_types[key] = text
        states[key] = "application_question"
        await bot.send_message(
            message.chat.id,
            f"{text}. Пришли одно фото и напиши вопрос в подписи к нему:",
            reply_markup=back_menu,
        )
    else:
        return False

    return True
