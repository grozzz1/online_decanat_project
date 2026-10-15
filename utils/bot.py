import logging
from asyncio import Queue, create_task, sleep, to_thread
from contextvars import ContextVar
from datetime import datetime
from re import sub
from traceback import format_exception

from aiohttp import ClientError
from telebot import util
from telebot.async_telebot import AsyncTeleBot, ExceptionHandler, logger
from telebot.asyncio_helper import ApiException, ApiTelegramException, RequestTimeout
from telebot.types import CallbackQuery, Message

from utils.states import application_types, dean_replies, states
from utils.users import get_name

current_message: ContextVar[Message | None] = ContextVar("current_message", default=None)
current_callback: ContextVar[CallbackQuery | None] = ContextVar("current_callback", default=None)
current_web_user = ContextVar("current_web_user", default=None)
current_ai_reply = ContextVar("current_ai_reply", default=False)


class BotErrors(ExceptionHandler):
    def __init__(self, bot):
        self.bot = bot

    async def handle(self, exception):
        self.bot.log_error(exception, "Ошибка Telegram или обработчика")
        return True


class Bot(AsyncTeleBot):
    def __init__(self, token, admin_log_chat_id):
        super().__init__(token, exception_handler=BotErrors(self))
        self.admin_log_chat_id = admin_log_chat_id
        self.log_queue = Queue()
        self.log_task = None
        self.web = None
        logger.disabled = False

    def start_logs(self):
        self.log_task = create_task(self.send_logs())
        self.write_log("Бот запущен.")

    def write_log(self, text):
        text = f"{datetime.now():%d.%m.%Y %H:%M:%S}\n{text}"
        text = text.replace(self.token, "[токен бота]")
        text = sub(r"(code=)[A-Za-z0-9_-]+", r"\1[код входа]", text)
        logging.getLogger("decanat.bot").info(text)
        self.log_queue.put_nowait(text)

    @staticmethod
    def get_context(message: Message | None = None) -> str:
        source = message if message is not None else current_message.get()

        if source is not None:
            user = source.from_user

            if user is None:
                return ""

            chat_id = source.chat.id
            message_id = source.message_id
            default = "главное меню" if source.chat.type == "private" else "деканат"
            state = states.get((chat_id, user.id), default)
        else:
            call = current_callback.get()

            if call is None or call.message is None:
                web = current_web_user.get()

                if web is not None:
                    return (
                        f"Пользователь: {get_name(web['user'])}\n"
                        f"ID пользователя: {web['user'].id}\n"
                        f"Источник: сайт\nОбращение: {web['request_id']}\n"
                        f"Документ: {web['application_type'] or 'обычный вопрос'}\n"
                    )

                return ""

            user = call.from_user
            chat_id = call.message.chat.id
            message_id = call.message.message_id
            state = "нажатие кнопки"

        key = (chat_id, user.id)
        state = "ответ студенту" if key in dean_replies else state
        text = (
            f"Пользователь: {get_name(user)}\n"
            f"ID пользователя: {user.id}\n"
            f"Сообщение пользователя: {message_id}\n"
            f"Состояние: {state}\n"
        )

        if key in dean_replies:
            text += f"Чат студента: {dean_replies[key]['chat_id']}\n"

            if dean_replies[key].get("source") == "web":
                text += f"Источник обращения: сайт\nОбращение: {dean_replies[key]['request_id']}\n"

        if key in application_types:
            text += f"Документ: {application_types[key]}\n"

        return text

    def log_callback(self, call):
        self.write_log(
            f"Нажатие кнопки\nЧат: {call.message.chat.id}\n{self.get_context()}\nДанные кнопки: {call.data}"
        )

    def log_message(self, action, message):
        if message.chat.id == self.admin_log_chat_id:
            return

        source = message if action == "Пользователь → бот" else None
        context = source or current_message.get()
        web = current_web_user.get()
        document_context = (
            current_ai_reply.get()
            or bool(web and web.get("application_type"))
            or bool(
                context is not None
                and context.from_user is not None
                and (context.chat.id, context.from_user.id) in application_types
            )
        )
        body = (
            "Содержимое документа не записывается в журнал."
            if document_context
            else message.text or message.caption or "Без текста"
        )
        text = (
            f"{action}\n"
            f"Чат: {message.chat.id}\n"
            f"Сообщение: {message.message_id}\n"
            f"Тип: {message.content_type}\n"
            f"{self.get_context(source)}\n"
            f"{body}"
        )

        if message.photo:
            text += f"\nФото: {message.photo[-1].file_id}"
        elif message.document:
            text += f"\nФайл: {message.document.file_name}\nID файла: {message.document.file_id}"

        self.write_log(text)

    def log_error(self, error, action, message=None):
        details = "".join(format_exception(type(error), error, error.__traceback__))
        self.write_log(f"{action}\n{self.get_context(message)}\n{details}")

    def handle_loop_error(self, _loop, context):
        error = context.get("exception")

        if error is None:
            self.write_log(f"Ошибка фоновой задачи: {context.get('message', 'Без описания')}")
        else:
            self.log_error(error, "Ошибка фоновой задачи")

    async def send_log(self, text):
        while True:
            try:
                await super().send_message(self.admin_log_chat_id, text, timeout=10)
                return
            except ApiTelegramException as error:
                if error.error_code >= 500:
                    await sleep(3)
                    continue

                if error.error_code != 429:
                    return

                delay = error.result_json.get("parameters", {}).get("retry_after", 3)
                await sleep(delay)
            except (ApiException, RequestTimeout, OSError, ClientError):
                await sleep(3)

    async def send_logs(self):
        while True:
            text = await self.log_queue.get()

            try:
                for part in util.split_string(text, chars_per_string=4096):
                    await self.send_log(part)
            finally:
                self.log_queue.task_done()

    async def send_message(self, chat_id, text, *args, **kwargs):
        sent = await super().send_message(chat_id, text, *args, **kwargs)
        await self.archive_message(sent)
        self.log_message("Бот → чат", sent)
        return sent

    async def send_photo(self, chat_id, photo, *args, **kwargs):
        sent = await super().send_photo(chat_id, photo, *args, **kwargs)
        await self.archive_message(sent)
        self.log_message("Бот → чат", sent)
        return sent

    async def send_document(self, chat_id, document, *args, **kwargs):
        sent = await super().send_document(chat_id, document, *args, **kwargs)
        await self.archive_message(sent)
        self.log_message("Бот → чат", sent)
        return sent

    async def answer_callback_query(self, callback_query_id, *args, **kwargs):
        result = await super().answer_callback_query(callback_query_id, *args, **kwargs)
        text = args[0] if args else kwargs.get("text")
        self.write_log(f"Ответ на нажатие кнопки\n{self.get_context()}\n{text or 'Кнопка обработана'}")
        return result

    async def edit_message_text(self, text=None, *args, **kwargs):
        edited = await super().edit_message_text(text, *args, **kwargs)

        if isinstance(edited, Message):
            await self.archive_message(edited)
            self.log_message("Бот изменил сообщение", edited)

        return edited

    async def archive_message(self, message, incoming=False):
        store = getattr(self.web, "store", None)
        if (
            store is None
            or message.chat.type != "private"
            or message.chat.id in (self.admin_log_chat_id, self.web.dean_chat_id)
        ):
            return
        user_id = message.from_user.id if incoming else message.chat.id
        if incoming:
            store.register_user(message.from_user)
        text = message.text or message.caption or ""
        role = (
            "student"
            if incoming
            else "ai"
            if current_ai_reply.get()
            else "dean"
            if text.startswith("Ответ деканата\n")
            else "bot"
        )
        media_tag = None
        attachment = message.photo[-1] if message.photo else message.document
        if attachment:
            size = getattr(attachment, "file_size", None)
            mimetype = "image/jpeg" if message.photo else getattr(attachment, "mime_type", "")
            if (not size or size <= 20 * 1024 * 1024) and mimetype in (
                "image/jpeg",
                "image/png",
                "application/pdf",
            ):
                try:
                    file = await self.get_file(attachment.file_id)
                    data = await self.download_file(file.file_path)
                    if len(data) <= 20 * 1024 * 1024:
                        from utils.documents import validate_document

                        mimetype = validate_document(data)
                        media_tag = await to_thread(store.storage.save_media, user_id, data, mimetype)
                except Exception as error:
                    self.log_error(error, "Не удалось сохранить вложение переписки")
                    text += "\nВложение недоступно для сохранения."
        store.add_message(
            user_id,
            role,
            text,
            media_tag=media_tag,
            source="telegram",
            external_id=f"telegram:{message.chat.id}:{message.message_id}",
        )

    async def _process_polling(self, *args, **kwargs):
        try:
            return await super()._process_polling(*args, **kwargs)
        except Exception as error:
            self.log_error(error, "Ошибка получения сообщений от Telegram")
            raise
