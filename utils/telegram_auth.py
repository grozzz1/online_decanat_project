import hashlib
import hmac
import json
import re
from time import time
from urllib.parse import parse_qsl


def validate_init_data(value, bot_token, *, max_age=300, now=None):
    failure = "Данные входа Telegram недействительны или устарели. Открой приложение заново."
    try:
        if not isinstance(value, str) or not value or len(value) > 16384:
            raise ValueError
        pairs = parse_qsl(value, keep_blank_values=True, strict_parsing=True)
        data = dict(pairs)
        if len(data) != len(pairs):
            raise ValueError
        received = data.pop("hash")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", received):
            raise ValueError
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        check = "\n".join(f"{key}={item}" for key, item in sorted(data.items()))
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received.lower()):
            raise ValueError
        age = (time() if now is None else now) - int(data["auth_date"])
        if age < -30 or age > max_age:
            raise ValueError
        user = json.loads(data["user"])
        if not isinstance(user, dict) or type(user.get("id")) is not int or not 0 < user["id"] < 2**52:
            raise ValueError
        if (
            not isinstance(user.get("first_name"), str)
            or not 1 <= len(user["first_name"]) <= 256
            or user.get("is_bot")
        ):
            raise ValueError
        for name in ("last_name", "username", "language_code"):
            if name in user and (not isinstance(user[name], str) or len(user[name]) > 256):
                raise ValueError
        return {**user, "is_bot": False}
    except (KeyError, ValueError, TypeError):
        raise ValueError(failure) from None
