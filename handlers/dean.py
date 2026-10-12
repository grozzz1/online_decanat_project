from telebot import util
from telebot.asyncio_helper import ApiException, RequestTimeout
from utils.callbacks import reply_target, web_reply_target
from utils.keyboards import cancel_reply_menu
from utils.states import dean_replies
from utils.users import get_name
from utils.validation import is_text


async def handle_callback(bot, call):
    key = (call.message.chat.id, call.from_user.id)

    if call.data.startswith(("reply:", "web_reply:")):
        try:
            if call.data.startswith("web_reply:"):
                request_id = web_reply_target(bot.token, call.data)
                web = getattr(bot, "web", None)
                target = web.store.reply_target(request_id) if web is not None else None

                if target is None:
                    await bot.answer_callback_query(
                        call.id, "Сессия сайта завершена. Ответить на это обращение уже нельзя.", show_alert=True,
                    )
                    return

                chat_id, message_id = target, None
            else:
                chat_id, message_id = reply_target(bot.token, call.data)
        except ValueError:
            await bot.answer_callback_query(call.id, "Эта кнопка ответа недействительна.", show_alert=True)
            return

        if key in dean_replies:
            await bot.answer_callback_query(
                call.id,
                "Сначала отправь текущий ответ или нажми «Отменить».",
                show_alert=True,
            )
            return

        await bot.answer_callback_query(call.id)
        prompt = await bot.send_message(
            call.message.chat.id,
            f"{get_name(call.from_user)}, напиши ответ студенту одним сообщением:",
            reply_to_message_id=call.message.message_id,
            allow_sending_without_reply=True,
            reply_markup=cancel_reply_menu(call.from_user.id),
        )
        dean_replies[key] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "prompt_id": prompt.message_id,
        }

        if call.data.startswith("web_reply:"):
            dean_replies[key].update(source="web", request_id=request_id)
    else:
        if call.data != f"cancel_reply:{call.from_user.id}":
            await bot.answer_callback_query(call.id, "Это ввод ответа другого сотрудника.", show_alert=True)
            return

        reply = dean_replies.get(key)

        if reply is None or reply["prompt_id"] != call.message.message_id:
            await bot.answer_callback_query(call.id, "Ввод ответа уже завершён.")
            return

        dean_replies.pop(key)
        await bot.answer_callback_query(call.id, "Ввод ответа отменён.")
        await bot.edit_message_text(
            "Ввод ответа отменён.",
            chat_id=call.message.chat.id,
            message_id=reply["prompt_id"],
            reply_markup=None,
        )


async def handle_reply(bot, message):
    key = (message.chat.id, message.from_user.id)
    reply = dean_replies[key]

    if is_text(message) and message.text.strip() in ("Отменить", "/cancel"):
        dean_replies.pop(key)
        await bot.edit_message_text(
            "Ввод ответа отменён.",
            chat_id=message.chat.id,
            message_id=reply["prompt_id"],
            reply_markup=None,
        )
        return

    if not is_text(message) or message.text.lstrip().startswith("/"):
        await bot.reply_to(message, "Напиши ответ обычным текстом или нажми «Отменить».")
        return

    text = f"Ответ деканата\nОтветил преподаватель: {get_name(message.from_user)}\n\n{message.text.strip()}"

    try:
        if reply.get("source") == "web":
            await bot.web.deliver_reply(reply["request_id"], text)
        else:
            for part in util.split_string(text, chars_per_string=4096):
                await bot.send_message(
                    reply["chat_id"],
                    part,
                    reply_to_message_id=reply["message_id"],
                    allow_sending_without_reply=True,
                )
    except (ApiException, RequestTimeout, OSError) as error:
        bot.log_error(error, "Ошибка отправки ответа студенту", message)
        await bot.reply_to(message, "Не удалось отправить ответ студенту. Попробуй ещё раз или нажми «Отменить».")
        return

    dean_replies.pop(key)
    await bot.edit_message_text(
        "Ответ отправлен студенту.",
        chat_id=message.chat.id,
        message_id=reply["prompt_id"],
        reply_markup=None,
    )
