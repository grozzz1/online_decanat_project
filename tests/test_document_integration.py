import asyncio
import unittest
from io import BytesIO
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telebot.types import User

from handlers.requests import accept_request, handle_request, send_application
from tests.fixtures import TEXTS, encoded, page
from utils.document_fields import DOCUMENT_SCHEMAS
from utils.states import application_types, clear_state, states
from utils.storage import Storage
from webapp.app import create_app


class WebIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.thread = Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.bot = SimpleNamespace(
            token="123:test",
            write_log=lambda *_: None,
            log_error=lambda *_: None,
            send_photo=AsyncMock(return_value=SimpleNamespace(message_id=1)),
            send_document=AsyncMock(return_value=SimpleNamespace(message_id=1)),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=2)),
        )
        self.data_directory = TemporaryDirectory()
        self.addCleanup(self.data_directory.cleanup)
        self.app = create_app(self.bot, self.loop, -1001, Storage(self.data_directory.name))
        self.app.config["TESTING"] = True
        self.service = self.app.extensions["decanat"]
        self.client = self.app.test_client()
        user = User(123, False, "Student")
        code = self.service.store.create_login(user)
        self.client.get(f"/login?code={code}")
        self.csrf = self.client.get("/api/session").json["csrf"]
        self.patches = [
            patch("utils.ocr.pytesseract.image_to_string", return_value=TEXTS["academic_leave_application"]),
            patch("utils.ocr.pytesseract.image_to_osd", return_value={}),
            patch("utils.ocr._ocr_words", return_value=[]),
        ]
        for mock in self.patches:
            mock.start()

    def tearDown(self):
        self.finish_processing()
        for mock in self.patches:
            mock.stop()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()
        self.data_directory.cleanup()

    def finish_processing(self):
        async def finish():
            if self.service.tasks:
                await asyncio.gather(*self.service.tasks)

        asyncio.run_coroutine_threadsafe(finish(), self.loop).result(timeout=20)

    def upload(self, data, name, application_type="Заявление на академический отпуск", mode="application"):
        return self.client.post(
            "/api/requests",
            data={
                "mode": mode,
                "text": "Вопрос о документе" if mode == "application_question" else "",
                "application_type": application_type,
                "photo": (BytesIO(data), name),
            },
            headers={"X-CSRF-Token": self.csrf},
        )

    def test_all_types_and_modes_reach_the_correct_extractor_and_logs(self):
        image, _ = page()
        for mode in ("application", "application_question"):
            for key, schema in DOCUMENT_SCHEMAS.items():
                with (
                    self.subTest(key=key, mode=mode),
                    patch(
                        "utils.ocr.pytesseract.image_to_string", return_value=TEXTS[key] + "\nOCR_BODY_ONLY"
                    ),
                    patch.object(self.bot, "write_log") as logs,
                ):
                    response = self.upload(encoded(image), "document.png", key, mode)
                    self.assertEqual(response.status_code, 202)
                    self.finish_processing()
                    item = self.client.get("/api/messages").json["requests"][-1]
                    self.assertEqual(item["document_type"], key)
                    self.assertEqual(item["application_type"], schema["label"])
                    self.assertEqual(item["extraction"]["document_type"], key)
                    self.assertEqual(set(item["fields"]), set(schema["fields"]))
                    self.assertIsNotNone(item["fields"]["student_name"])
                    self.assertFalse(item["validation"]["fields"]["blocking"])
                    reports = [
                        call.args[0] for call in logs.call_args_list if "ОБРАБОТКА ДОКУМЕНТА" in call.args[0]
                    ]
                    self.assertEqual(len(reports), 1)
                    for field in schema["fields"]:
                        self.assertIn(field, reports[0])
                    self.assertNotIn(item["fields"]["student_name"], reports[0])
                    if mode == "application":
                        notification = self.bot.send_photo.await_args.kwargs["caption"] + "\n".join(
                            call.args[1] for call in self.bot.send_message.await_args_list
                        )
                        self.assertIn("student_name", notification)
                        self.assertIn(item["fields"]["student_name"], notification)
                        self.assertNotIn("OCR_BODY_ONLY", notification)
                        self.assertNotIn("Текст документа:", notification)

    def test_unknown_document_type_is_rejected(self):
        self.assertEqual(self.upload(encoded(page()[0]), "document.png", "invented_type").status_code, 400)

    def test_ocr_failure_reports_all_fields_and_does_not_stop_next_request(self):
        with (
            patch(
                "utils.ocr.pytesseract.image_to_string", side_effect=RuntimeError("token=SECRET code=LOGIN")
            ),
            patch.object(self.bot, "write_log") as logs,
        ):
            self.upload(encoded(page()[0]), "document.png", "study_certificate")
            self.finish_processing()
            item = self.client.get("/api/messages").json["requests"][-1]
            self.assertEqual(item["status"], "failed")
            self.assertEqual(len(item["fields"]), 10)
            self.assertTrue(all(value is None for value in item["fields"].values()))
            reports = [call.args[0] for call in logs.call_args_list if "ОБРАБОТКА ДОКУМЕНТА" in call.args[0]]
            self.assertIn("RuntimeError", reports[0])
            self.assertIn("0/10", reports[0])
            self.assertNotIn("SECRET", reports[0])
            self.assertNotIn("LOGIN", reports[0])
        with patch("utils.ocr.pytesseract.image_to_string", return_value=TEXTS["internship_certificate"]):
            response = self.upload(encoded(page()[0]), "next.png", "internship_certificate")
            self.assertEqual(response.status_code, 202)
            self.finish_processing()
        self.assertEqual(self.client.get("/api/messages").json["requests"][-1]["status"], "sent")

    def test_login_link_and_existing_authentication(self):
        response = self.app.test_client().get("/login")
        html = response.data.decode("utf-8")
        self.assertIn('href="https://t.me/OnlineDecanatBot">нашему боту в Telegram</a>', html)
        self.assertIn("Отправь <strong>/start</strong>", html)
        self.assertEqual(self.client.get("/api/session").status_code, 200)

    def test_extraction_error_preserves_ocr_and_document_delivery(self):
        with (
            patch("utils.ocr.extract_document_fields", side_effect=RuntimeError("secret field values")),
            patch.object(self.bot, "write_log") as logs,
        ):
            self.upload(encoded(page()[0]), "document.png", "academic_leave_application")
            self.finish_processing()
            item = self.client.get("/api/messages").json["requests"][-1]
            self.assertEqual(item["status"], "sent")
            self.assertEqual(item["recognized"], TEXTS["academic_leave_application"].strip())
            self.assertEqual(len(item["fields"]), 8)
            self.assertTrue(all(value is None for value in item["fields"].values()))
            self.assertEqual(item["extraction"]["errors"], [{"stage": "extraction", "code": "RuntimeError"}])
            reports = [call.args[0] for call in logs.call_args_list if "ОБРАБОТКА ДОКУМЕНТА" in call.args[0]]
            self.assertIn("ERROR", reports[0])
            self.assertNotIn("secret field values", reports[0])
            self.bot.send_photo.assert_awaited_once()

    def test_image_upload_analysis_and_preview(self):
        image, _ = page(stamp=True, signature=True)
        original = encoded(image)
        response = self.upload(original, "document.png")
        self.assertEqual(response.status_code, 202)
        self.finish_processing()
        item = self.client.get("/api/messages").json["requests"][0]
        self.assertEqual(item["status"], "sent")
        self.assertTrue(item["visuals"]["stamp"]["detected"])
        self.assertFalse(item["validation"]["blocking"])
        self.assertNotIn("previews", item)
        preview = self.client.get(f"/api/requests/{item['id']}/pages/1/photo")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.mimetype, "image/jpeg")
        with BytesIO(preview.data) as buffer:
            from PIL import Image

            with Image.open(buffer) as loaded:
                self.assertEqual(loaded.size, image.size)
        self.assertEqual(self.client.get(f"/api/requests/{item['id']}/photo").data, original)
        self.bot.send_photo.assert_awaited_once()

    def test_pdf_is_forwarded_as_document_and_each_page_is_accessible(self):
        first, _ = page(stamp=True)
        second, _ = page(signature=True)
        buffer = BytesIO()
        first.save(buffer, "PDF", save_all=True, append_images=[second], resolution=100)
        original = buffer.getvalue()
        response = self.upload(original, "document.pdf")
        self.assertEqual(response.status_code, 202)
        self.finish_processing()
        item = self.client.get("/api/messages").json["requests"][0]
        self.assertEqual(item["status"], "sent")
        self.assertEqual(len(item["visuals"]["pages"]), 2)
        for number in (1, 2):
            self.assertEqual(
                self.client.get(f"/api/requests/{item['id']}/pages/{number}/photo").status_code, 200
            )
        self.assertEqual(self.client.get(f"/api/requests/{item['id']}/pages/3/photo").status_code, 404)
        self.assertEqual(self.client.get(f"/api/requests/{item['id']}/photo").data, original)
        self.bot.send_document.assert_awaited_once()
        self.bot.send_photo.assert_not_awaited()

    def test_reject_invalid_content_and_missing_csrf(self):
        response = self.upload(b"corrupt", "document.pdf")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post("/api/requests", data={"mode": "application"}).status_code, 403)

    def test_preview_access_is_restricted_to_owner(self):
        image, _ = page(stamp=True)
        response = self.upload(encoded(image), "document.png")
        self.finish_processing()
        request_id = response.json["request_id"]
        another = self.app.test_client()
        self.assertEqual(another.get(f"/api/requests/{request_id}/pages/1/photo").status_code, 401)
        code = self.service.store.create_login(User(456, False, "Other"))
        another.get(f"/login?code={code}")
        self.assertEqual(another.get(f"/api/requests/{request_id}/pages/1/photo").status_code, 404)


class BotIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def document_bot(self):
        image, _ = page()
        data = encoded(image)
        bot = SimpleNamespace(
            token="123:test",
            get_file=AsyncMock(return_value=SimpleNamespace(file_path="document.png")),
            download_file=AsyncMock(return_value=data),
            write_log=Mock(),
            log_error=Mock(),
            send_photo=AsyncMock(return_value=SimpleNamespace(message_id=1)),
            send_document=AsyncMock(),
            send_message=AsyncMock(),
            reply_to=AsyncMock(),
        )
        message = SimpleNamespace(
            photo=None,
            document=SimpleNamespace(
                file_id="test", file_size=len(data), mime_type="image/png", file_name="document.png"
            ),
            content_type="document",
            media_group_id=None,
            from_user=User(123, False, "Student"),
            caption=None,
            chat=SimpleNamespace(id=123),
        )
        return bot, message

    async def test_all_document_types_log_their_full_schema(self):
        key = (123, 123)
        for document_type, schema in DOCUMENT_SCHEMAS.items():
            with self.subTest(document_type=document_type):
                bot, message = self.document_bot()
                states[key] = "application_photo"
                application_types[key] = schema["label"]
                try:
                    with (
                        patch(
                            "utils.ocr.pytesseract.image_to_string",
                            return_value=TEXTS[document_type] + "\nOCR_BODY_ONLY",
                        ),
                        patch("utils.ocr.pytesseract.image_to_osd", return_value={}),
                        patch("utils.ocr._ocr_words", return_value=[]),
                    ):
                        await send_application(bot, message, key, -1001)
                    reports = [call.args[0] for call in bot.write_log.call_args_list]
                    self.assertEqual(len(reports), 1)
                    self.assertIn(document_type, reports[0])
                    for field in schema["fields"]:
                        self.assertIn(field, reports[0])
                    self.assertNotIn("Иванов", reports[0])
                    self.assertNotIn("Сидорова", reports[0])
                    self.assertNotIn("Петров", reports[0])
                    notification = bot.send_photo.await_args.kwargs["caption"] + "\n".join(
                        call.args[1] for call in bot.send_message.await_args_list
                    )
                    self.assertIn("student_name", notification)
                    self.assertNotIn("OCR_BODY_ONLY", notification)
                    self.assertNotIn("Текст документа:", notification)
                finally:
                    clear_state(key)

    async def test_ocr_error_preserves_bot_state_and_next_attempt_works(self):
        key = (123, 123)
        bot, message = self.document_bot()
        states[key] = "application_photo"
        application_types[key] = "Справка об обучении"
        try:
            with (
                patch("utils.ocr.pytesseract.image_to_string", side_effect=RuntimeError("sensitive text")),
                patch("utils.ocr.pytesseract.image_to_osd", return_value={}),
            ):
                await accept_request(bot, message, key, -1001)
            self.assertEqual(states[key], "application_photo")
            self.assertIn("0/10", bot.write_log.call_args.args[0])
            self.assertNotIn("sensitive text", bot.write_log.call_args.args[0])
            with (
                patch("utils.ocr.pytesseract.image_to_string", return_value=TEXTS["study_certificate"]),
                patch("utils.ocr.pytesseract.image_to_osd", return_value={}),
                patch("utils.ocr._ocr_words", return_value=[]),
            ):
                await accept_request(bot, message, key, -1001)
            self.assertNotIn(key, states)
            bot.send_photo.assert_awaited_once()
        finally:
            clear_state(key)

    async def test_unsupported_file_returns_hint_without_downloading(self):
        key = (123, 123)
        bot, message = self.document_bot()
        message.document.mime_type = "application/zip"
        message.document.file_name = "archive.zip"
        states[key] = "application_photo"
        application_types[key] = "Справка об обучении"
        try:
            await handle_request(bot, message, -1001)
            bot.get_file.assert_not_awaited()
            bot.reply_to.assert_awaited_once()
            self.assertEqual(states[key], "application_photo")
        finally:
            clear_state(key)

    async def test_pdf_attachment_and_visual_summary(self):
        first, _ = page(stamp=True)
        buffer = BytesIO()
        first.save(buffer, "PDF")
        bot = SimpleNamespace(
            token="123:test",
            get_file=AsyncMock(return_value=SimpleNamespace(file_path="document.pdf")),
            download_file=AsyncMock(return_value=buffer.getvalue()),
            send_document=AsyncMock(return_value=SimpleNamespace(message_id=1)),
            send_photo=AsyncMock(),
            send_message=AsyncMock(),
            reply_to=AsyncMock(),
        )
        message = SimpleNamespace(
            photo=None,
            document=SimpleNamespace(file_id="test", file_size=len(buffer.getvalue())),
            from_user=User(123, False, "Student"),
            chat=SimpleNamespace(id=123),
            caption=None,
        )
        key = (123, 123)
        states[key] = "application_photo"
        application_types[key] = "Заявление на академический отпуск"
        try:
            with (
                patch("utils.ocr.pytesseract.image_to_string", return_value="Текст"),
                patch("utils.ocr.pytesseract.image_to_osd", return_value={}),
                patch("utils.ocr._ocr_words", return_value=[]),
            ):
                await send_application(bot, message, key, -1001)
            bot.send_document.assert_awaited_once()
            bot.send_photo.assert_not_awaited()
            summary = bot.reply_to.await_args.args[1]
            self.assertIn("эвристика", summary)
            self.assertIn("Печать", summary)
        finally:
            clear_state(key)


if __name__ == "__main__":
    unittest.main()
