from hashlib import sha256
from hmac import compare_digest, new


def reply_data(token, chat_id, message_id):
    data = f"reply:{chat_id}:{message_id}"
    signature = new(token.encode(), data.encode(), sha256).hexdigest()[:16]
    return f"{data}:{signature}"


def reply_target(token, callback_data):
    data, signature = callback_data.rsplit(":", 1)
    expected = new(token.encode(), data.encode(), sha256).hexdigest()[:16]

    if not compare_digest(signature, expected):
        raise ValueError("Неверная кнопка ответа.")

    action, chat_id, message_id = data.split(":")

    if action != "reply":
        raise ValueError("Неверная кнопка ответа.")

    return int(chat_id), int(message_id)


def web_reply_data(token, request_id):
    data = f"web_reply:{request_id}"
    signature = new(token.encode(), data.encode(), sha256).hexdigest()[:16]
    return f"{data}:{signature}"


def web_reply_target(token, callback_data):
    data, signature = callback_data.rsplit(":", 1)
    expected = new(token.encode(), data.encode(), sha256).hexdigest()[:16]

    if not compare_digest(signature, expected):
        raise ValueError("Неверная кнопка ответа.")

    action, request_id = data.split(":")

    if action != "web_reply":
        raise ValueError("Неверная кнопка ответа.")

    return request_id
