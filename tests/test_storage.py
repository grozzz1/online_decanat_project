import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from sqlalchemy import select

from utils.application_types import ApplicationTypes
from utils.database import settings
from utils.storage import Storage
from webapp.store import WebStore


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = Storage(self.directory.name)

    def test_history_and_profiles_survive_reopen_and_are_isolated(self):
        user = SimpleNamespace(id=123, first_name="Студент", username="student")
        store = WebStore(self.storage)
        store.create_login(user)
        store.add_message(
            123, "student", "Вопрос из Telegram", source="telegram", external_id="telegram:123:1"
        )
        store.add_message(456, "student", "Чужая переписка")
        restored = WebStore(Storage(self.directory.name))
        self.assertEqual(restored.get_user(123).first_name, "Студент")
        messages = restored.snapshot(123)["messages"]
        self.assertEqual([item["text"] for item in messages], ["Вопрос из Telegram"])
        self.assertEqual(messages[0]["source"], "telegram")
        self.assertEqual(restored.snapshot(123, messages[0]["id"])["messages"], [])
        self.assertEqual(store.storage.secret_key(), restored.storage.secret_key())

    def test_failed_transaction_rolls_back_and_next_connection_remains_usable(self):
        with self.assertRaises(RuntimeError):
            with self.storage.connection() as connection:
                connection.execute(settings.insert().values(key="rollback", value="pending"))
                raise RuntimeError("interrupted write")
        with self.storage.connection() as connection:
            self.assertIsNone(connection.scalar(select(settings.c.value).where(settings.c.key == "rollback")))
        self.storage.add_message(123, "student", "После отменённой транзакции")
        self.assertEqual(Storage(self.directory.name).messages(123)[0]["text"], "После отменённой транзакции")

    def test_global_limit_is_1000_and_old_message_is_retained(self):
        first = self.storage.save_media(123, b"old photo", "image/jpeg")
        message = self.storage.add_message(123, "student", "Старое фото", media_tag=first)
        for number in range(1000):
            self.storage.save_media(456, str(number).encode(), "image/jpeg")
        self.assertEqual(len(list(self.storage.media_directory.iterdir())), 1000)
        self.assertIsNone(self.storage.read_media(first, 123))
        restored = Storage(self.directory.name).get_message(123, message["id"])
        self.assertEqual(restored["text"], "Старое фото")
        self.assertTrue(restored["photo_expired"])

    def test_concurrent_writes_keep_limit_and_unique_names(self):
        storage = Storage(self.directory.name, media_limit=5)
        with ThreadPoolExecutor(max_workers=4) as workers:
            tags = list(
                workers.map(lambda number: storage.save_media(number, b"image", "image/png"), range(25))
            )
        self.assertEqual(len(set(tags)), 25)
        self.assertEqual(len(list(storage.media_directory.iterdir())), 5)

    def test_tags_cannot_escape_media_directory_and_foreign_owner_cannot_read(self):
        tag = self.storage.save_media(123, b"image", "image/png")
        self.assertIsNone(self.storage.read_media(tag, 456))
        for tag in ("../private.png", "/etc/passwd", "C:\\Windows\\file.png"):
            with self.assertRaises(ValueError):
                self.storage.media_path(tag)

    def test_replayed_telegram_message_does_not_duplicate_history(self):
        first = self.storage.add_message(123, "student", "Исходный текст", external_id="telegram:123:5")
        second = self.storage.add_message(123, "student", "Изменённый текст", external_id="telegram:123:5")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.storage.messages(123)), 1)
        self.assertEqual(self.storage.messages(123)[0]["text"], "Изменённый текст")

    def test_types_persist_and_defaults_cannot_be_deleted(self):
        types = ApplicationTypes(self.storage)
        key = types.add("Заявление на перевод", ["application_question"], 42)
        restored = ApplicationTypes(Storage(self.directory.name))
        self.assertIsNotNone(restored.resolve(key, "application_question"))
        self.assertIsNone(restored.resolve(key, "application"))
        with self.assertRaises(ValueError):
            restored.delete("academic_leave_application")
        self.assertTrue(restored.delete(key))
        self.assertIsNone(restored.resolve(key))

    def test_interrupted_processing_is_not_resubmitted_after_restart(self):
        store = WebStore(self.storage)
        user = SimpleNamespace(id=123, first_name="Student")
        store.create_login(user)
        request_id = store.create_request(123, "question", "Вопрос", "", None, None)
        restored = WebStore(Storage(self.directory.name))
        item = restored.get_request(123, request_id)
        self.assertEqual(item["status"], "failed")
        self.assertFalse(item["retry_allowed"])
        self.assertIn("перезапуск", item["failure_message"])
