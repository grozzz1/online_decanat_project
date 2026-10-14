from io import BytesIO
from telebot import util
from telebot.asyncio_helper import ApiException, RequestTimeout
from utils import delivery
from utils.keyboards import (
    application_questions_menu,
    applications_menu,
    answer_student_menu,
    back_menu,
    main_menu,
)
from utils.documents import MAX_BYTES, validate_document
from utils.ocr import analyze_document_async
from utils.states import application_types, clear_state, states
from utils.validation import has_caption, is_document, is_text


async def send_question(bot, message, dean_chat_id):
    await delivery.send_question(
        bot, dean_chat_id, message.from_user, message.text,
        answer_student_menu(bot.token, message),
    )


async def send_application(bot, message, key, dean_chat_id):
    attachment = message.photo[-1] if message.photo else message.document
    if attachment.file_size and attachment.file_size > MAX_BYTES:
        raise ValueError("Документ должен быть не больше 10 МБ.")
    file = await bot.get_file(attachment.file_id)

    with BytesIO(await bot.download_file(file.file_path)) as photo:
        mimetype = validate_document(photo.getvalue())
        analysis = await analyze_document_async(photo, application_types[key])
        recognized = analysis["recognized"]
        question = message.caption if states[key] == "application_question" else None
        markup = answer_student_menu(bot.token, message) if question is not None else None
        await delivery.send_application(
            bot, dean_chat_id, message.from_user, photo, application_types[key],
            recognized, question, markup, visuals=analysis["visuals"], mimetype=mimetype,
        )
        for part in util.split_string(delivery.visual_summary(analysis["visuals"]), chars_per_string=4096):
            await bot.reply_to(message, part)


async def accept_request(bot, message, key, dean_chat_id):
    state = states[key]

    try:
        if state == "dean_question":
            await send_question(bot, message, dean_chat_id)
        else:
            await send_application(bot, message, key, dean_chat_id)
    except ValueError as error:
        await bot.reply_to(message, str(error), reply_markup=back_menu)
        return
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
        if is_document(message):
            await accept_request(bot, message, key, dean_chat_id)
            return

        await bot.reply_to(
            message,
            "Пришли один документ: фото, JPG, PNG или PDF до 10 МБ, без альбома:",
            reply_markup=back_menu,
        )
    elif state == "application_question":
        if is_document(message) and has_caption(message):
            await accept_request(bot, message, key, dean_chat_id)
            return

        await bot.reply_to(
            message,
            "Нужен один документ (фото, JPG, PNG или PDF до 10 МБ) с вопросом в подписи:",
            reply_markup=back_menu,
        )
    else:
        await bot.send_message(
            message.chat.id,
            "Выбери пункт меню:",
            reply_markup=main_menu,
        )
