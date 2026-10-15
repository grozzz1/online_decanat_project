import hashlib
import hmac
import json
import unittest
from urllib.parse import urlencode

from utils.telegram_auth import validate_init_data


def signed_data(token="123:test", *, user_id=123, auth_date=1000):
    values = {
        "auth_date": str(auth_date),
        "query_id": "test-query",
        "user": json.dumps({"id": user_id, "first_name": "Student"}, separators=(",", ":")),
    }
    key = hmac.digest(b"WebAppData", token.encode(), "sha256")
    values["hash"] = hmac.new(
        key, "\n".join(f"{name}={value}" for name, value in sorted(values.items())).encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(values)


class TelegramAuthTests(unittest.TestCase):
    def test_valid_signature_returns_verified_identity(self):
        self.assertEqual(validate_init_data(signed_data(), "123:test", now=1100)["id"], 123)

    def test_forged_identity_wrong_bot_and_expired_data_are_rejected(self):
        for value, token, now in (
            (signed_data().replace("123", "456"), "123:test", 1100),
            (signed_data(), "456:other", 1100),
            (signed_data(), "123:test", 1400),
            (signed_data(auth_date=2000), "123:test", 1000),
        ):
            with self.subTest(value=value, now=now), self.assertRaises(ValueError):
                validate_init_data(value, token, now=now)

    def test_duplicate_fields_and_non_numeric_user_id_are_rejected(self):
        for value in (signed_data() + "&user=another", signed_data(user_id="123"), signed_data(user_id=True)):
            with self.assertRaises(ValueError):
                validate_init_data(value, "123:test", now=1100)
