from io import BytesIO

from telebot import util
from telebot.asyncio_helper import ApiException, RequestTimeout

from utils import delivery
from utils.bot import current_ai_reply
from utils.document_context import remember_document
from utils.document_fields import document_label, normalize_document_type
from utils.document_logging import log_document_failure, log_document_result
from utils.documents import MAX_BYTES, validate_document
from utils.keyboards import (
    answer_student_menu,
    application_questions_menu,
    applications_menu,
    back_menu,
    main_menu,
)
from utils.ocr import analyze_document_async
from utils.question_answering import answer_question_async, format_answer
from utils.states import application_types, clear_state, states
from utils.validation import has_caption, is_document, is_text


async def send_question(bot, message, dean_chat_id):
    await delivery.send_question(
        bot,
        dean_chat_id,
        message.from_user,
        message.text,
        answer_student_menu(bot.token, message),
    )


async def send_ai_answer(bot, message, text, extraction):
    privacy = current_ai_reply.set(True)
    try:
        answer = await answer_question_async(
            {"question_id": str(message.message_id), "question": text}, extraction
        )
        for part in util.split_string(format_answer(answer), chars_per_string=4096):
            await bot.reply_to(message, part)
    except Exception as error:
        if hasattr(bot, "write_log"):
            bot.write_log("AI question failed: " + type(error).__name__)
        await bot.reply_to(message, "AI-помощник не смог проверить ответ. Обратитесь в деканат.")
    finally:
        current_ai_reply.reset(privacy)


async def send_application(bot, message, key, dean_chat_id):
    attachment = message.photo[-1] if message.photo else message.document
    if attachment.file_size and attachment.file_size > MAX_BYTES:
        raise ValueError("Документ должен быть не больше 10 МБ.")
    file = await bot.get_file(attachment.file_id)

    with BytesIO(await bot.download_file(file.file_path)) as photo:
        mimetype = validate_document(photo.getvalue())
        try:
            analysis = await analyze_document_async(photo, normalize_document_type(application_types[key]))
        except Exception as error:
            log_document_failure(application_types[key], error, getattr(bot, "write_log", None))
            raise
        log_document_result(analysis["extraction"], getattr(bot, "write_log", None))
        recognized = analysis["recognized"]
        question = message.caption if states[key] == "application_question" else None
        if question is not None:
            remember_document((message.chat.id, message.from_user.id), analysis["extraction"])
            await send_ai_answer(bot, message, question, analysis["extraction"])
            return
        markup = answer_student_menu(bot.token, message) if question is not None else None
        await delivery.send_application(
            bot,
            dean_chat_id,
            message.from_user,
            photo,
            document_label(analysis["document_type"]),
            recognized,
            question,
            markup,
            visuals=analysis["visuals"],
            mimetype=mimetype,
        )
        for part in util.split_string(delivery.visual_summary(analysis["visuals"]), chars_per_string=4096):
            await bot.reply_to(message, part)
        remember_document((message.chat.id, message.from_user.id), analysis["extraction"])


async def accept_request(bot, message, key, dean_chat_id):
    state = states[key]

    try:
        if state == "dean_question":
            await send_question(bot, message, dean_chat_id)
        else:
            await send_application(bot, message, key, dean_chat_id)
    except ValueError as error:
        if state != "dean_question":
            log_document_failure(
                application_types[key], error, getattr(bot, "write_log", None), stage="input"
            )
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
        "Документ отправлен в деканат." if state == "application_photo"
        else "Вопрос обработан AI-помощником." if state == "application_question"
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
