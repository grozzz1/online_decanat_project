from asyncio import Lock

from telebot import util

from utils.bot import current_callback, current_message
from utils.states import dean_replies

from . import dean, menu, requests, types

locks = {}


def register_handlers(bot, dean_chat_id):
    @bot.message_handler(
        commands=["add_type", "remove_type", "types"],
        func=lambda message: message.chat.id == dean_chat_id and message.from_user is not None,
        chat_types=["group", "supergroup"],
    )
    async def receive_type_command(message):
        key = (message.chat.id, message.from_user.id)
        async with locks.setdefault(key, Lock()):
            try:
                await types.command(bot, message)
            except ValueError as error:
                await bot.reply_to(message, str(error))

    @bot.callback_query_handler(
        func=lambda call: (
            call.message is not None
            and call.message.chat.id == dean_chat_id
            and bool(call.data)
            and call.data.startswith("doctype:")
        )
    )
    async def receive_type_callback(call):
        key = (call.message.chat.id, call.from_user.id)
        async with locks.setdefault(key, Lock()):
            try:
                await types.callback(bot, call)
            except ValueError as error:
                await bot.answer_callback_query(call.id, str(error), show_alert=True)

    @bot.callback_query_handler(
        func=lambda call: (
            call.message is not None
            and call.message.chat.id == dean_chat_id
            and bool(call.data)
            and call.data.startswith(("reply:", "web_reply:", "cancel_reply:"))
        ),
    )
    async def receive_callback(call):
        key = (call.message.chat.id, call.from_user.id)

        async with locks.setdefault(key, Lock()):
            token = current_callback.set(call)

            try:
                bot.log_callback(call)
                await dean.handle_callback(bot, call)
            except Exception as error:
                bot.log_error(error, "Ошибка нажатия кнопки в деканате")
                await bot.answer_callback_query(call.id, "Не получилось обработать кнопку. Попробуй ещё раз.")
            finally:
                current_callback.reset(token)

    @bot.message_handler(
        func=lambda message: (
            message.chat.id == dean_chat_id
            and message.from_user is not None
            and (message.chat.id, message.from_user.id) in dean_replies
        ),
        content_types=util.content_type_media,
        chat_types=["group", "supergroup"],
    )
    async def receive_reply(message):
        key = (message.chat.id, message.from_user.id)

        async with locks.setdefault(key, Lock()):
            if key not in dean_replies:
                return

            token = current_message.set(message)

            try:
                bot.log_message("Деканат → бот", message)
                await dean.handle_reply(bot, message)
            except Exception as error:
                bot.log_error(error, "Ошибка обработки ответа деканата", message)
                await bot.reply_to(
                    message, "Не получилось обработать ответ. Попробуй ещё раз или нажми «Отменить»."
                )
            finally:
                current_message.reset(token)

    @bot.message_handler(
        func=lambda message: True,
        content_types=util.content_type_media,
        chat_types=["private"],
    )
    async def receive_message(message):
        key = (message.chat.id, message.from_user.id)

        async with locks.setdefault(key, Lock()):
            token = current_message.set(message)

            try:
                if getattr(type(bot), "archive_message", None) is not None:
                    await bot.archive_message(message, incoming=True)
                bot.log_message("Пользователь → бот", message)
                if await menu.handle_menu(bot, message):
                    return

                await requests.handle_request(bot, message, dean_chat_id)
            except Exception as error:
                bot.log_error(error, "Ошибка обработки сообщения", message)
                await bot.reply_to(
                    message,
                    "Не получилось обработать сообщение. Попробуй ещё раз чуть позже.",
                )
            finally:
                current_message.reset(token)
