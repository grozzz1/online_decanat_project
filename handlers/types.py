from secrets import token_hex
from time import monotonic

from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

pending = {}


def registry(bot):
    web = getattr(bot, "web", None)
    if web is None or not hasattr(web, "store"):
        raise ValueError("Сервис ещё запускается. Попробуй немного позже.")
    return web.store.types


async def command(bot, message):
    name, *arguments = message.text.split(maxsplit=1)
    name = name.split("@", 1)[0]
    types = registry(bot)
    key = (message.chat.id, message.from_user.id)
    if name == "/add_type":
        if not arguments:
            await bot.reply_to(message, "Добавление типа: /add_type Название заявления")
            return
        label = " ".join(arguments[0].split())
        if not 1 <= len(label) <= 80 or label.startswith("/"):
            raise ValueError("Название должно содержать от 1 до 80 символов.")
        nonce = token_hex(6)
        pending[key] = (label, nonce, monotonic() + 600)
        markup = InlineKeyboardMarkup()
        for mode, text in (
            ("application", "Сдача заявления"),
            ("application_question", "Вопрос по заявлению"),
            ("both", "Оба раздела"),
        ):
            markup.add(InlineKeyboardButton(text, callback_data=f"doctype:add:{mode}:{nonce}"))
        await bot.reply_to(message, f"Где показывать «{label}»?", reply_markup=markup)
    elif name == "/remove_type" and arguments:
        if types.delete(arguments[0].strip()):
            await bot.reply_to(message, "Тип удалён из выбора. История обращений сохранена.")
        else:
            raise ValueError("Пользовательский тип не найден.")
    else:
        markup = InlineKeyboardMarkup()
        lines = ["Типы документов:"]
        for item in types.list():
            sections = ", ".join(
                "сдача заявления" if mode == "application" else "вопрос по заявлению"
                for mode in item["modes"]
            )
            lines.append(f"{item['label']} — {sections}" + (" (стандартный)" if not item["custom"] else ""))
            if item["custom"]:
                markup.add(
                    InlineKeyboardButton(
                        f"Удалить: {item['label']}", callback_data=f"doctype:remove:{item['key']}"
                    )
                )
        await bot.reply_to(message, "\n".join(lines), reply_markup=markup)


async def callback(bot, call):
    parts = call.data.split(":")
    types = registry(bot)
    if len(parts) == 4 and parts[:2] == ["doctype", "add"]:
        key = (call.message.chat.id, call.from_user.id)
        entry = pending.get(key)
        if not entry or entry[1] != parts[3] or entry[2] < monotonic():
            raise ValueError("Выбор устарел или открыт другим сотрудником. Повтори /add_type.")
        modes = ["application", "application_question"] if parts[2] == "both" else [parts[2]]
        types.add(entry[0], modes, call.from_user.id)
        pending.pop(key, None)
        await bot.answer_callback_query(call.id, "Тип добавлен")
        await bot.edit_message_text(
            f"Тип «{entry[0]}» добавлен. Он уже доступен в боте и на сайте.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None,
        )
    elif len(parts) == 3 and parts[:2] == ["doctype", "remove"]:
        removed = types.delete(parts[2])
        await bot.answer_callback_query(
            call.id, "Тип удалён. История сохранена." if removed else "Тип уже удалён."
        )
        await bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    else:
        raise ValueError("Недействительная команда управления типами.")
