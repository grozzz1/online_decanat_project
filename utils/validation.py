from telebot.types import Message


def is_text(message: Message) -> bool:
    return message.content_type == "text" and bool(
        message.text and message.text.strip()
    )


def is_photo(message: Message) -> bool:
    return (
        message.content_type == "photo"
        and bool(message.photo)
        and message.media_group_id is None
    )


def has_caption(message: Message) -> bool:
    return bool(message.caption and message.caption.strip())
