from asyncio import Task, create_task, to_thread
from io import BytesIO

from telebot import util

from utils import delivery
from utils.bot import current_ai_reply, current_document
from utils.document_context import remember_document
from utils.document_feedback import document_feedback
from utils.document_fields import document_label, normalize_document_type
from utils.document_logging import log_document_failure, log_document_result
from utils.documents import MAX_BYTES, validate_document
from utils.keyboards import (
    answer_student_menu,
    available_document_types,
    back_menu,
    document_menu,
    main_menu,
)
from utils.ocr import analyze_document_async
from utils.question_answering import answer_question_async, format_answer
from utils.states import application_types, clear_state, states
from utils.validation import has_caption, is_document, is_text


async def send_question(bot, message, dean_chat_id):
    if getattr(type(bot), "archive_message", None) is not None:
        await bot.archive_message(message, incoming=True)
    task = create_task(
        deliver_to_dean(
            bot,
            message,
            delivery.send_question(
                bot, dean_chat_id, message.from_user, message.text, answer_student_menu(bot.token, message)
            ),
        )
    )
    try:
        await send_ai_answer(bot, message, message.text, None, dean_chat_id, task)
    finally:
        message_id = await task
    return message_id is not None


async def deliver_to_dean(bot, message, operation):
    try:
        return await operation
    except Exception as error:
        bot.log_error(error, "Не удалось подтвердить доставку обращения в деканат", message)
        await bot.reply_to(
            message,
            "Не удалось подтвердить доставку обращения в деканат. Результат проверки доступен здесь. "
            "Перед повторной отправкой уточни, получено ли обращение сотрудниками.",
            reply_markup=main_menu,
        )
        return None


async def send_ai_answer(bot, message, text, extraction, dean_chat_id, dean_message_id):
    try:
        answer = await answer_question_async(
            {"question_id": str(message.message_id), "question": text}, extraction
        )
        response = format_answer(answer)
    except Exception as error:
        if hasattr(bot, "write_log"):
            bot.write_log("AI question failed: " + type(error).__name__)
        response = "AI-помощник не смог проверить ответ. Обратись в деканат."
    privacy = current_ai_reply.set(True)
    try:
        for part in util.split_string(response, chars_per_string=4096):
            await bot.reply_to(message, part)
    finally:
        current_ai_reply.reset(privacy)
        if isinstance(dean_message_id, Task):
            dean_message_id = await dean_message_id
        if dean_message_id is not None:
            await delivery.notify_ai_answer(bot, dean_chat_id, dean_message_id, response)


async def send_application(bot, message, key, dean_chat_id, *, selection=None):
    state, application_type = selection or (states[key], application_types[key])
    attachment = message.photo[-1] if message.photo else message.document
    if attachment.file_size and attachment.file_size > MAX_BYTES:
        raise ValueError("Документ должен быть не больше 10 МБ.")
    mode = "application_question" if state == "application_question" else "application"
    selected = next(
        (
            item
            for item in available_document_types(bot, mode)
            if application_type in (item["label"], item["key"])
            or item["key"] == normalize_document_type(application_type)
        ),
        None,
    )
    if selected is None:
        raise ValueError("Этот тип документа удалён. Вернись в меню и выбери другой.")

    file = await bot.get_file(attachment.file_id)
    with BytesIO(await bot.download_file(file.file_path)) as photo:
        mimetype = await to_thread(validate_document, photo.getvalue())
        try:
            analysis = await analyze_document_async(photo, selected["key"])
        except Exception as error:
            log_document_failure(application_type, error, getattr(bot, "write_log", None))
            raise
        log_document_result(analysis["extraction"], getattr(bot, "write_log", None))
        for part in util.split_string(document_feedback(analysis), chars_per_string=4096):
            await bot.reply_to(message, part)
        for part in util.split_string(delivery.visual_summary(analysis["visuals"]), chars_per_string=4096):
            await bot.reply_to(message, part)
        question = message.caption if state == "application_question" else None
        if question is not None:
            if getattr(type(bot), "archive_message", None) is not None:
                await bot.archive_message(message, incoming=True)
        markup = answer_student_menu(bot.token, message) if question is not None else None
        task = create_task(
            deliver_to_dean(
                bot,
                message,
                delivery.send_application(
                    bot,
                    dean_chat_id,
                    message.from_user,
                    photo,
                    selected["label"] if selected["custom"] else document_label(analysis["document_type"]),
                    analysis["extraction"],
                    question,
                    markup,
                    visuals=analysis["visuals"],
                    mimetype=mimetype,
                    file_id=attachment.file_id,
                ),
            )
        )
        try:
            if question is not None:
                await send_ai_answer(bot, message, question, analysis["extraction"], dean_chat_id, task)
        finally:
            message_id = await task
        remember_document(key, analysis["extraction"])
        return message_id is not None


async def accept_request(bot, message, key, dean_chat_id, *, selection=None):
    state, application_type = selection or (states[key], application_types.get(key))
    token = current_document.set(application_type)
    retry_menu = main_menu if selection is not None else back_menu

    try:
        if state == "dean_question":
            delivered = await send_question(bot, message, dean_chat_id)
        else:
            delivered = await send_application(
                bot, message, key, dean_chat_id, selection=(state, application_type)
            )
    except ValueError as error:
        if state != "dean_question":
            log_document_failure(application_type, error, getattr(bot, "write_log", None), stage="input")
        await bot.reply_to(message, str(error), reply_markup=retry_menu)
        return
    except Exception as error:
        bot.log_error(error, "Ошибка отправки обращения", message)
        await bot.reply_to(
            message,
            "Не удалось отправить обращение. Попробуй ещё раз чуть позже:",
            reply_markup=retry_menu,
        )
        return
    finally:
        current_document.reset(token)

    if delivered is False:
        return
    if selection is None:
        clear_state(key)
    text = "Документ отправлен в деканат." if state == "application_photo" else "Вопрос отправлен в деканат."
    await bot.reply_to(message, text, reply_markup=main_menu)


async def begin_request(bot, message, key, dean_chat_id):
    tasks = getattr(bot, "request_tasks", None)
    if tasks is None:
        tasks = bot.request_tasks = {}
    if key in tasks:
        await bot.reply_to(message, "Предыдущее обращение ещё обрабатывается. Результат придёт сюда.")
        return
    selection = (states[key], application_types.get(key))
    await bot.reply_to(
        message,
        "Обращение принято в обработку. Результат придёт сюда; пока можно пользоваться меню.",
        reply_markup=main_menu,
    )
    clear_state(key)
    task = create_task(accept_request(bot, message, key, dean_chat_id, selection=selection))
    tasks[key] = task

    def finish(completed):
        if tasks.get(key) is completed:
            tasks.pop(key)
        if not completed.cancelled() and completed.exception() is not None:
            bot.log_error(completed.exception(), "Ошибка фоновой обработки обращения", message)

    task.add_done_callback(finish)


async def handle_request(bot, message, dean_chat_id):
    key = (message.chat.id, message.from_user.id)
    state = states.get(key)

    if state == "dean_question":
        if is_text(message) and not message.text.lstrip().startswith("/"):
            await begin_request(bot, message, key, dean_chat_id)
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
            reply_markup=document_menu(bot, "application"),
        )
    elif state == "application_question_type":
        await bot.reply_to(
            message,
            "Выбери документ кнопкой ниже:",
            reply_markup=document_menu(bot, "application_question"),
        )
    elif state == "application_photo":
        if is_document(message):
            await begin_request(bot, message, key, dean_chat_id)
            return

        await bot.reply_to(
            message,
            "Пришли один документ: фото, JPG, PNG или PDF до 10 МБ, без альбома:",
            reply_markup=back_menu,
        )
    elif state == "application_question":
        if is_document(message) and has_caption(message):
            await begin_request(bot, message, key, dean_chat_id)
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
