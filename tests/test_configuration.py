import os
import traceback
import unittest
from unittest.mock import patch

from utils.config import get_dean_chat_id, get_tesseract_command, get_token


class ConfigurationTests(unittest.TestCase):
    def test_missing_token_has_actionable_message_without_secret(self):
        with patch.dict(os.environ, {"BOT_TOKEN": ""}):
            with self.assertRaisesRegex(ValueError, r"BOT_TOKEN.*\.env"):
                get_token()

    def test_example_token_is_rejected(self):
        with patch.dict(os.environ, {"BOT_TOKEN": "replace_with_botfather_token"}):
            with self.assertRaises(ValueError):
                get_token()

    def test_invalid_chat_id_does_not_echo_input(self):
        with patch.dict(os.environ, {"DEAN_CHAT_ID": "PRIVATE_VALUE"}):
            with self.assertRaises(ValueError) as caught:
                get_dean_chat_id()
            self.assertNotIn("PRIVATE_VALUE", str(caught.exception))
            self.assertNotIn("PRIVATE_VALUE", "".join(traceback.format_exception(caught.exception)))

    def test_tesseract_uses_explicit_path_then_path_lookup(self):
        with patch.dict(os.environ, {"TESSERACT_CMD": "custom/tesseract.exe"}):
            self.assertEqual(get_tesseract_command(), "custom/tesseract.exe")
        with (
            patch.dict(os.environ, {"TESSERACT_CMD": ""}),
            patch("utils.config.Path.is_file", return_value=False),
            patch("utils.config.which", return_value="/usr/bin/tesseract"),
        ):
            self.assertEqual(get_tesseract_command(), "/usr/bin/tesseract")
