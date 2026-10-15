from telebot.types import Message

from utils.document_validation import validate_document_fields as validate_document_fields
from utils.document_validation import validate_document_visuals as validate_document_visuals


def is_text(message: Message) -> bool:
    return message.content_type == "text" and bool(message.text and message.text.strip())


def is_photo(message: Message) -> bool:
    return message.content_type == "photo" and bool(message.photo) and message.media_group_id is None


def has_caption(message: Message) -> bool:
    return bool(message.caption and message.caption.strip())


def is_document(message: Message) -> bool:
    return is_photo(message) or (
        message.content_type == "document"
        and message.document is not None
        and message.media_group_id is None
        and (
            message.document.mime_type in ("image/jpeg", "image/png", "application/pdf")
            or (message.document.file_name or "").lower().endswith((".jpg", ".jpeg", ".png", ".pdf"))
        )
    )
