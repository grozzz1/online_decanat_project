from telebot import util
from utils.users import get_username


async def send_question(bot, dean_chat_id, user, question, markup, source=""):
    text = f"Получен вопрос от пользователя {get_username(user)}:\n\n{question.strip()}"

    if source:
        text += f"\n\nИсточник: {source}"

    parts = util.split_string(text, chars_per_string=4096)
    first = await bot.send_message(dean_chat_id, parts[0], reply_markup=markup)

    for part in parts[1:]:
        await bot.send_message(dean_chat_id, part, reply_to_message_id=first.message_id)


def visual_summary(visuals):
    if not visuals:
        return ""
    lines = ["Визуальные признаки (эвристика, подлинность не проверяется):"]
    if not visuals["detections"]:
        incomplete = any(page.get("analysis_status") == "unavailable" for page in visuals["pages"])
        lines.append("Визуальный анализ недоступен для части страниц." if incomplete else "Печать и подпись не обнаружены алгоритмом.")
    for item in visuals["detections"][:15]:
        label = "Печать" if item["type"] == "stamp" else "Подпись студента" if item.get("role") == "student" else "Подпись: роль неизвестна"
        status = "сомнительный кандидат" if item["status"] == "uncertain" else "визуальный кандидат"
        lines.append(f"{label}: {status}, страница {item['page']}, score {item['confidence']:.2f}, bbox {item['bbox']}.")
    if visuals["warnings"]:
        lines.extend(visuals["warnings"])
    return "\n".join(lines)


async def send_application(bot, dean_chat_id, user, photo, application_type, recognized, question=None, markup=None, source="", visuals=None, mimetype="image/jpeg"):
    text = (
        f"Получено заявление от пользователя {get_username(user)}\n"
        f"Тип: {application_type}"
    )

    if source:
        text += f"\nИсточник: {source}"

    if question is not None:
        text += f"\n\nВопрос: {question.strip()}"

    if visuals:
        text += f"\n\n{visual_summary(visuals)}"
    text += f"\n\nТекст документа:\n{recognized or 'Не удалось распознать текст.'}"
    photo.seek(0)
    if mimetype == "application/pdf":
        sent = await bot.send_document(dean_chat_id, photo, visible_file_name="document.pdf", caption=text[:1024], reply_markup=markup)
    else:
        sent = await bot.send_photo(dean_chat_id, photo, caption=text[:1024], reply_markup=markup)

    for part in util.split_string(text[1024:], chars_per_string=4096):
        await bot.send_message(dean_chat_id, part, reply_to_message_id=sent.message_id)
