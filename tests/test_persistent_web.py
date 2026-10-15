import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telebot.types import Message, User

from tests import test_document_integration
from tests.fixtures import encoded, page
from tests.test_telegram_auth import signed_data
from utils.bot import Bot
from utils.storage import Storage
from webapp.app import create_app


class PersistentWebTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_document_integration.WebIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_telegram_message_and_photo_appear_in_web_history(self):
        fixture = self.fixture
        bot = Bot("123:test", -999)
        bot.web = fixture.service
        data = encoded(page()[0])
        bot.get_file = AsyncMock(return_value=SimpleNamespace(file_path="photo.jpg"))
        bot.download_file = AsyncMock(return_value=data)
        message = Message.de_json(
            {
                "message_id": 20,
                "date": 0,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 123, "is_bot": False, "first_name": "Student"},
                "caption": "Фото из бота",
                "photo": [
                    {
                        "file_id": "test",
                        "file_unique_id": "unique",
                        "width": 800,
                        "height": 1100,
                        "file_size": len(data),
                    }
                ],
            }
        )
        asyncio.run(bot.archive_message(message, incoming=True))
        item = fixture.client.get("/api/messages").json["messages"][-1]
        self.assertEqual(item["text"], "Фото из бота")
        self.assertEqual(item["source"], "telegram")
        response = fixture.client.get(f"/api/messages/{item['id']}/photo")
        self.assertEqual(response.data, data)

    def test_web_history_and_original_file_survive_new_app(self):
        fixture = self.fixture
        data = encoded(page()[0])
        request_id = fixture.upload(data, "document.png").json["request_id"]
        fixture.finish_processing()
        storage = Storage(fixture.data_directory.name)
        restored = create_app(fixture.bot, fixture.loop, -1001, storage)
        client = restored.test_client()
        code = restored.extensions["decanat"].store.create_login(User(123, False, "Student"))
        client.get(f"/login?code={code}")
        history = client.get("/api/messages").json
        self.assertEqual(history["requests"][0]["id"], request_id)
        self.assertEqual(history["requests"][0]["status"], "sent")
        self.assertEqual(client.get(f"/api/requests/{request_id}/photo").data, data)
        self.assertEqual(len(list(storage.media_directory.iterdir())), 1)

    def test_evicted_photo_returns_410_without_removing_text(self):
        fixture = self.fixture
        storage = fixture.service.store.storage
        storage.media_limit = 2
        request_id = fixture.upload(encoded(page()[0]), "old.png").json["request_id"]
        fixture.finish_processing()
        message_id = fixture.client.get("/api/messages").json["messages"][0]["id"]
        storage.save_media(456, b"second", "image/png")
        storage.save_media(456, b"third", "image/png")
        for path in (
            f"/api/messages/{message_id}/photo",
            f"/api/requests/{request_id}/photo",
            f"/api/requests/{request_id}/pages/1/photo",
        ):
            response = fixture.client.get(path)
            self.assertEqual(response.status_code, 410, path)
            self.assertIn("1000", response.json["error"])
        messages = fixture.client.get("/api/messages").json["messages"]
        self.assertTrue(messages[0]["photo_expired"])
        self.assertTrue(messages[0]["text"])

    def test_other_student_cannot_fetch_attachment(self):
        fixture = self.fixture
        fixture.upload(encoded(page()[0]), "private.png")
        fixture.finish_processing()
        message_id = fixture.client.get("/api/messages").json["messages"][0]["id"]
        client = fixture.app.test_client()
        code = fixture.service.store.create_login(User(456, False, "Other"))
        client.get(f"/login?code={code}")
        self.assertEqual(client.get(f"/api/messages/{message_id}/photo").status_code, 404)
        self.assertEqual(client.get("/api/messages").json["messages"], [])

    def test_custom_type_accepts_only_selected_modes_and_keeps_history_after_delete(self):
        fixture = self.fixture
        types = fixture.service.store.types
        key = types.add("Заявление на перевод", ["application_question"], 42)
        data = encoded(page()[0])
        self.assertEqual(fixture.upload(data, "document.png", key, "application").status_code, 400)
        response = fixture.upload(data, "document.png", key, "application_question")
        self.assertEqual(response.status_code, 202)
        fixture.finish_processing()
        item = fixture.client.get("/api/messages").json["requests"][-1]
        self.assertEqual(item["status"], "checked", item.get("failure_message"))
        self.assertEqual(item["application_type"], "Заявление на перевод")
        self.assertEqual(item["preliminary_check"]["status"], "unknown")
        self.assertTrue(types.delete(key))
        self.assertEqual(fixture.client.get("/api/messages").json["requests"][-1]["id"], item["id"])
        self.assertEqual(fixture.upload(data, "new.png", key, "application_question").status_code, 400)

    def test_miniapp_authentication_uses_signature_and_preserves_csrf(self):
        fixture = self.fixture
        client = fixture.app.test_client()
        with patch("webapp.app.get_token", return_value="123:test"):
            bad = client.post("/api/auth/telegram", json={"init_data": "user=123&hash=fake"})
            self.assertEqual(bad.status_code, 401)
            self.assertEqual(client.get("/api/session").status_code, 401)
            good = client.post(
                "/api/auth/telegram", json={"init_data": signed_data(auth_date=int(time.time()))}
            )
            self.assertEqual(good.status_code, 200)
        self.assertEqual(client.get("/api/session").json["user"]["name"], "Student")
        self.assertEqual(client.post("/api/logout").status_code, 403)
        response = client.get("/")
        self.assertNotIn("X-Frame-Options", response.headers)
        self.assertIn("https://web.telegram.org", response.headers["Content-Security-Policy"])

    def test_https_miniapp_uses_partitioned_secure_cookie(self):
        fixture = self.fixture
        fixture.app.config["SESSION_COOKIE_SECURE"] = True
        client = fixture.app.test_client()
        with patch("webapp.app.get_token", return_value="123:test"):
            response = client.post(
                "/api/auth/telegram",
                base_url="https://decanat.example",
                json={"init_data": signed_data(auth_date=int(time.time()))},
            )
        cookie = response.headers["Set-Cookie"]
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=None", cookie)
        self.assertIn("Partitioned", cookie)
