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


def is_document(message: Message) -> bool:
    return is_photo(message) or (
        message.content_type == "document" and message.document is not None
        and message.media_group_id is None
        and (message.document.mime_type in ("image/jpeg", "image/png", "application/pdf")
             or (message.document.file_name or "").lower().endswith((".jpg", ".jpeg", ".png", ".pdf")))
    )


def validate_document_visuals(application_type, visuals):
    # The repository has no rules requiring stamps/signatures for any document type.
    # Report the evidence; do not create rejection rules based on CV guesses.
    return {"application_type": application_type, "status": "informational", "blocking": False,
            "required_elements": [], "stamp": visuals["stamp"],
            "student_signature": visuals["student_signature"],
            "manual_review": bool(visuals["warnings"]) or any(
                item["status"] == "uncertain" or item.get("role") == "unknown"
                for item in visuals["detections"])}
