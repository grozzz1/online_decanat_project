import asyncio
import json
import os
import subprocess
import sys
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts.doctor import _telegram_health, check

ROOT = Path(__file__).resolve().parents[1]


class DoctorTests(unittest.TestCase):
    def test_missing_dependencies_produce_json_without_importing_site_packages(self):
        process = subprocess.run(
            [sys.executable, "-S", str(ROOT / "scripts" / "doctor.py"), "--skip-telegram-config"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            env={**os.environ, "PYTHONPATH": ""},
        )
        self.assertEqual(process.returncode, 1, process.stderr)
        report = json.loads(process.stdout)
        self.assertFalse(report["ok"])
        self.assertIn("Flask", report["checks"]["dependencies"]["missing"])
        self.assertIn("requirements.txt", report["checks"]["dependencies"]["help"])
        self.assertNotIn("Traceback", process.stderr)

    def test_one_missing_package_preserves_other_versions(self):
        def installed(name):
            if name == "Pillow":
                raise PackageNotFoundError(name)
            return "test-version"

        with patch("scripts.doctor.version", side_effect=installed):
            report = check()
        self.assertEqual(report["checks"]["dependencies"]["missing"], ["Pillow"])
        self.assertEqual(report["packages"]["Flask"], "test-version")
        self.assertIsNone(report["packages"]["Pillow"])

    def test_webhook_blocks_polling_and_client_session_is_closed(self):
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="example_bot")),
            get_webhook_info=AsyncMock(return_value=SimpleNamespace(url="https://example.invalid/webhook")),
            close_session=AsyncMock(),
        )
        with (
            patch("telebot.async_telebot.AsyncTeleBot", return_value=bot),
            patch("utils.config.get_token", return_value="test-token"),
        ):
            report = asyncio.run(_telegram_health())
        self.assertFalse(report["ok"])
        self.assertTrue(report["webhook_enabled"])
        bot.close_session.assert_awaited_once()

    def test_network_failure_does_not_echo_secret_and_closes_session(self):
        secret = "private-token-must-not-appear"
        bot = SimpleNamespace(
            get_me=AsyncMock(side_effect=RuntimeError(secret)),
            get_webhook_info=AsyncMock(return_value=SimpleNamespace(url="")),
            close_session=AsyncMock(),
        )
        with (
            patch("telebot.async_telebot.AsyncTeleBot", return_value=bot),
            patch.dict(os.environ, {"BOT_TOKEN": secret, "DEAN_CHAT_ID": "-1", "ADMIN_LOG_CHAT_ID": "-2"}),
        ):
            report = check(telegram=True)
        self.assertEqual(report["checks"]["telegram_api"], {"ok": False, "error_type": "RuntimeError"})
        self.assertNotIn(secret, json.dumps(report))
        bot.close_session.assert_awaited_once()
