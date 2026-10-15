import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from handlers import dean
from handlers.requests import send_application, send_question
from tests import test_document_integration as integration
from tests.fixtures import TEXTS, encoded, page
from utils.question_answering import QuestionEngine
from utils.states import application_types, clear_state, dean_replies, states


class StudentScenariosTests(unittest.TestCase):
    setUp = integration.WebIntegrationTests.setUp
    tearDown = integration.WebIntegrationTests.tearDown
    finish_processing = integration.WebIntegrationTests.finish_processing
    upload = integration.WebIntegrationTests.upload

    def test_question_delivered_and_staff_reply_survives_refresh_without_ai(self):
        with patch("webapp.service.answer_question_async", new_callable=AsyncMock) as ai:
            response = self.client.post(
                "/api/requests",
                data={"mode": "question", "text": "Когда приём?"},
                headers={"X-CSRF-Token": self.csrf},
            )
            self.assertEqual(response.status_code, 202)
            self.finish_processing()
            request_id = response.json["request_id"]
            self.assertEqual(self.bot.send_message.await_args.args[0], -1001)
            self.assertIn("Когда приём?", self.bot.send_message.await_args.args[1])
            ai.assert_not_awaited()
        self.bot.edit_message_text = AsyncMock()
        key = (-1001, 789)
        dean_replies[key] = {"source": "web", "request_id": request_id, "prompt_id": 42}
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-1001),
            from_user=SimpleNamespace(id=789, first_name="Сотрудник", last_name=None, username=None),
            text="Завтра в 10:00",
            content_type="text",
        )
        try:
            self.service.call(dean.handle_reply(self.bot, message))
        finally:
            dean_replies.pop(key, None)
        for _ in range(2):
            messages = self.client.get("/api/messages").json["messages"]
            self.assertEqual(messages[-1]["role"], "dean")
            self.assertIn("Завтра в 10:00", messages[-1]["text"])
        self.assertIsNone(self.service.store.get_request(999, request_id))

    def test_document_question_uses_python_and_retains_attachment_for_followup(self):
        with patch(
            "utils.local_ai.LocalAI.generate", side_effect=AssertionError("No LLM for typical questions")
        ):
            response = self.client.post(
                "/api/requests",
                data={
                    "mode": "application_question",
                    "text": "Кто принимает окончательное решение?",
                    "application_type": "academic_leave_application",
                    "photo": (BytesIO(encoded(page()[0])), "document.png"),
                },
                headers={"X-CSRF-Token": self.csrf},
            )
            self.assertEqual(response.status_code, 202)
            self.finish_processing()
            item = self.client.get("/api/messages").json["requests"][-1]
            self.assertEqual(item["status"], "checked")
            self.assertIsNotNone(item["extraction"])
            self.assertIsNotNone(item["validation"])
            self.assertEqual(item["ai_status"], "done")
            self.assertEqual(item["ai_answer"]["result"]["sources"], ["rule:1.3"])
            self.assertFalse(item["ai_answer"]["diagnostics"]["llm_called"])
            self.assertIsNone(self.service.store.reply_target(item["id"]))
            self.bot.send_photo.assert_not_awaited()
            self.bot.send_document.assert_not_awaited()
            self.bot.send_message.assert_not_awaited()
            with patch(
                "webapp.service.analyze_document_async", side_effect=AssertionError("No repeated OCR")
            ):
                response = self.client.post(
                    f"/api/requests/{item['id']}/ask",
                    json={"question": "Какой курс указан?"},
                    headers={"X-CSRF-Token": self.csrf},
                )
                self.assertEqual(response.status_code, 202)
                self.finish_processing()
            retained = self.service.store.get_request(123, item["id"])
            self.assertEqual(retained["photo"], encoded(page()[0]))
            self.assertEqual(retained["ai_answer"]["result"]["sources"], ["field:course"])
            self.assertEqual(self.client.get("/api/messages").json["messages"][-1]["role"], "ai")

    def test_document_question_requires_text_and_file(self):
        for data in ({"photo": (BytesIO(encoded(page()[0])), "document.png")}, {"text": "Вопрос"}):
            response = self.client.post(
                "/api/requests",
                data={
                    "mode": "application_question",
                    "application_type": "academic_leave_application",
                    **data,
                },
                headers={"X-CSRF-Token": self.csrf},
            )
            self.assertEqual(response.status_code, 400)

    def test_complex_document_question_uses_existing_rag_and_checked_sources(self):
        def select_sources(prompt, payload, schema, **kwargs):
            return {"question_id": payload["question_id"], "answerable": True, "sources": ["rule:2.1"]}

        client = SimpleNamespace(generate=Mock(side_effect=select_sources))
        engine = QuestionEngine(client=client)
        with patch("utils.question_answering.get_question_engine", return_value=engine):
            response = self.client.post(
                "/api/requests",
                data={
                    "mode": "application_question",
                    "application_type": "academic_leave_application",
                    "text": "Какие источники ответа разрешены сервису?",
                    "photo": (BytesIO(encoded(page()[0])), "document.png"),
                },
                headers={"X-CSRF-Token": self.csrf},
            )
            self.assertEqual(response.status_code, 202)
            self.finish_processing()
        item = self.service.store.get_request(123, response.json["request_id"])
        answer = item["ai_answer"]
        self.assertTrue(answer["diagnostics"]["rag_called"])
        self.assertTrue(answer["diagnostics"]["llm_called"])
        self.assertTrue(answer["diagnostics"]["verified"])
        self.assertEqual(answer["result"]["sources"], ["rule:2.1"])
        client.generate.assert_called_once()
        self.bot.send_photo.assert_not_awaited()

    def test_application_has_preliminary_result_and_cannot_start_ai_chat(self):
        with patch("webapp.service.answer_question_async", new_callable=AsyncMock) as ai:
            response = self.upload(encoded(page()[0]), "document.png")
            self.finish_processing()
            item = self.service.store.get_request(123, response.json["request_id"])
            self.assertEqual(item["status"], "sent")
            self.assertIn(item["preliminary_check"]["status"], ("document_ok", "missing_fields", "unknown"))
            rejected = self.client.post(
                f"/api/requests/{item['id']}/ask",
                json={"question": "Какой курс?"},
                headers={"X-CSRF-Token": self.csrf},
            )
            self.assertEqual(rejected.status_code, 409)
            ai.assert_not_awaited()
            self.bot.send_photo.assert_awaited_once()

    def test_document_options_and_no_ai_controls(self):
        html = self.client.get("/").get_data(as_text=True)
        for kind in ("study_certificate", "academic_leave_application", "internship_certificate"):
            self.assertIn(f'value="{kind}"', html)
        self.assertNotIn('data-application-visible="false"', html)
        self.assertNotIn('id="ai-question"', html)
        self.assertNotIn('id="ask-ai"', html)


class TelegramQuestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_document_question_calls_ai_without_staff_delivery(self):
        bot, message = integration.BotIntegrationTests.document_bot(self)
        message.caption = "Кто принимает окончательное решение?"
        key = (message.chat.id, message.from_user.id)
        states[key] = "application_question"
        application_types[key] = "academic_leave_application"
        try:
            with (
                patch(
                    "utils.ocr.pytesseract.image_to_string", return_value=TEXTS["academic_leave_application"]
                ),
                patch("utils.ocr.pytesseract.image_to_osd", return_value={}),
                patch("utils.ocr._ocr_words", return_value=[]),
                patch("handlers.requests.send_ai_answer", new_callable=AsyncMock) as ai,
            ):
                await send_application(bot, message, key, -1001)
                ai.assert_awaited_once()
                bot.send_photo.assert_not_awaited()
                bot.send_document.assert_not_awaited()
        finally:
            clear_state(key)

    async def test_staff_question_does_not_call_ai(self):
        bot = SimpleNamespace(token="123:test", send_message=AsyncMock())
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=123, first_name="Student", last_name=None, username=None),
            chat=SimpleNamespace(id=123),
            message_id=5,
            text="Вопрос",
        )
        with patch("handlers.requests.send_ai_answer", new_callable=AsyncMock) as ai:
            await send_question(bot, message, -1001)
            bot.send_message.assert_awaited_once()
            ai.assert_not_awaited()
