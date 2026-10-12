from io import BytesIO
from telebot.asyncio_helper import ApiException, RequestTimeout
from utils import delivery
from utils.keyboards import (
    application_questions_menu,
    applications_menu,
    answer_student_menu,
    back_menu,
    main_menu,
)
from utils.ocr import recognize_text_async
from utils.states import application_types, clear_state, states
from utils.validation import has_caption, is_photo, is_text


async def send_question(bot, message, dean_chat_id):
    await delivery.send_question(
        bot, dean_chat_id, message.from_user, message.text,
        answer_student_menu(bot.token, message),
    )


async def send_application(bot, message, key, dean_chat_id):
    file = await bot.get_file(message.photo[-1].file_id)

    with BytesIO(await bot.download_file(file.file_path)) as photo:
        recognized = await recognize_text_async(photo)
        question = message.caption if states[key] == "application_question" else None
        markup = answer_student_menu(bot.token, message) if question is not None else None
        await delivery.send_application(
            bot, dean_chat_id, message.from_user, photo, application_types[key],
            recognized, question, markup,
        )


async def accept_request(bot, message, key, dean_chat_id):
    state = states[key]

    try:
        if state == "dean_question":
            await send_question(bot, message, dean_chat_id)
        else:
            await send_application(bot, message, key, dean_chat_id)
    except (ApiException, RequestTimeout, OSError, RuntimeError) as error:
        bot.log_error(error, "Ошибка отправки обращения", message)
        await bot.reply_to(
            message,
            "Не удалось отправить обращение. Попробуй ещё раз чуть позже:",
            reply_markup=back_menu,
        )
        return

    clear_state(key)
    text = (
        "Заявление отправлено в деканат."
        if state == "application_photo"
        else "Вопрос отправлен в деканат."
    )
    await bot.reply_to(message, text, reply_markup=main_menu)


async def handle_request(bot, message, dean_chat_id):
    key = (message.chat.id, message.from_user.id)
    state = states.get(key)

    if state == "dean_question":
        if is_text(message) and not message.text.lstrip().startswith("/"):
            await accept_request(bot, message, key, dean_chat_id)
            return

        await bot.reply_to(
            message,
            "Напиши вопрос обычным текстом. Сообщение не должно быть пустым или командой:",
            reply_markup=back_menu,
        )
    elif state == "application_type":
        await bot.reply_to(
            message,
            "Выбери заявление кнопкой ниже:",
            reply_markup=applications_menu,
        )
    elif state == "application_question_type":
        await bot.reply_to(
            message,
            "Выбери документ кнопкой ниже:",
            reply_markup=application_questions_menu,
        )
    elif state == "application_photo":
        if is_photo(message):
            await accept_request(bot, message, key, dean_chat_id)
            return

        await bot.reply_to(
            message,
            "Нужно одно фото. Отправь изображение как фотографию, а не как файл или альбом:",
            reply_markup=back_menu,
        )
    elif state == "application_question":
        if is_photo(message) and has_caption(message):
            await accept_request(bot, message, key, dean_chat_id)
            return

        await bot.reply_to(
            message,
            "Нужно одно фото с вопросом в подписи. Фото и текст отправь одним сообщением:",
            reply_markup=back_menu,
        )
    else:
        await bot.send_message(
            message.chat.id,
            "Выбери пункт меню:",
            reply_markup=main_menu,
        )
