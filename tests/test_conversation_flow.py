import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telebot.types import Message

from handlers import register_handlers
from handlers.requests import send_question
from tests import test_document_integration as integration
from tests.fixtures import encoded, page
from utils.bot import Bot, current_message
from utils.keyboards import BACK, DEAN_QUESTION
from utils.states import clear_state


def telegram_message(text, message_id=10):
    return Message.de_json(
        {
            "message_id": message_id,
            "date": 0,
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 123, "is_bot": False, "first_name": "Student"},
            "text": text,
        }
    )


class ConversationWebTests(unittest.TestCase):
    setUp = integration.WebIntegrationTests.setUp
    tearDown = integration.WebIntegrationTests.tearDown
    finish_processing = integration.WebIntegrationTests.finish_processing
    upload = integration.WebIntegrationTests.upload

    def test_document_question_reaches_staff_when_ai_fails_and_staff_can_reply(self):
        with patch("webapp.service.answer_question_async", side_effect=RuntimeError("unavailable")):
            response = self.upload(encoded(page()[0]), "document.png", mode="application_question")
            self.finish_processing()
        request_id = response.json["request_id"]
        item = self.service.store.get_request(123, request_id)
        self.assertEqual(item["status"], "checked")
        self.assertEqual(item["ai_status"], "failed")
        self.assertEqual(self.service.store.reply_target(request_id), 123)
        self.bot.send_photo.assert_awaited_once()
        photo = self.bot.send_photo.await_args
        self.assertIsNotNone(photo.kwargs["reply_markup"])
        notification = "\n".join(call.args[1] for call in self.bot.send_message.await_args_list)
        self.assertIn("student_name", photo.kwargs["caption"] + notification)
        self.assertIn("AI-помощник не смог", notification)
        self.service.call(self.service.deliver_reply(request_id, "Ответ сотрудника"))
        messages = self.client.get("/api/messages").json["messages"]
        self.assertEqual([item["role"] for item in messages], ["student", "ai", "dean"])
        followup = self.client.post(
            f"/api/requests/{request_id}/ask",
            json={"question": "Какой курс указан?"},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.assertEqual(followup.status_code, 202)
        self.finish_processing()
        self.assertEqual(self.service.store.reply_target(request_id), 123)

    def test_ordinary_question_uses_ai_without_document_and_keeps_staff_button(self):
        response = self.client.post(
            "/api/requests",
            data={"mode": "question", "text": "Кто принимает окончательное решение?"},
            headers={"X-CSRF-Token": self.csrf},
        )
        self.finish_processing()
        item = self.service.store.get_request(123, response.json["request_id"])
        self.assertEqual(item["ai_answer"]["result"]["sources"], ["rule:1.3"])
        self.assertEqual(item["ai_status"], "done")
        self.assertEqual(self.service.store.reply_target(item["id"]), 123)
        self.assertIsNotNone(self.bot.send_message.await_args_list[0].kwargs["reply_markup"])
        self.assertTrue(
            any("Ответ AI студенту" in call.args[1] for call in self.bot.send_message.await_args_list)
        )

    def test_old_commands_and_menu_responses_are_hidden_from_history(self):
        store = self.service.store
        for text in ("/start", "/start payload", "/cancel", BACK, DEAN_QUESTION):
            store.add_message(123, "student", text, source="telegram")
        store.add_message(123, "bot", "Выбери пункт меню")
        store.add_message(123, "student", "Когда можно забрать справку?", source="telegram")
        store.add_message(123, "ai", "Уточните в деканате", source="telegram")
        store.add_message(123, "dean", "Завтра", source="telegram")
        messages = self.client.get("/api/messages").json["messages"]
        self.assertEqual([item["role"] for item in messages], ["student", "ai", "dean"])
        self.assertEqual(messages[0]["text"], "Когда можно забрать справку?")

    def test_not_found_and_expired_login_do_not_spam_errors_but_server_error_is_logged(self):
        with patch.object(self.bot, "log_error") as errors:
            for path in ("/obsolete-token", "/api/requests/missing/photo", "/api/messages/99999/photo"):
                self.assertEqual(self.client.get(path).status_code, 404)
            stale = self.app.test_client().get("/login?code=expired")
            self.assertEqual(stale.status_code, 200)
            self.assertIn("недействителен", stale.get_data(as_text=True))
            errors.assert_not_called()
            with patch.object(self.service.store, "snapshot", side_effect=RuntimeError("internal")):
                self.assertEqual(self.client.get("/api/messages").status_code, 500)
            errors.assert_called_once()

    def test_login_accepts_the_raw_code(self):
        code = self.service.store.create_login(self.service.store.get_user(123))
        client = self.app.test_client()
        html = client.get("/login").get_data(as_text=True)
        self.assertIn('placeholder="Код"', html)
        self.assertNotIn("Код после", html)
        self.assertEqual(client.post("/login", data={"code": code}).status_code, 302)
        self.assertEqual(client.get("/api/session").status_code, 200)


class ConversationTelegramTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_has_copyable_code_and_navigation_is_not_archived(self):
        bot = Bot("123:test", -999)
        code = "a" * 32
        bot.web = SimpleNamespace(
            create_login=AsyncMock(return_value=f"https://decanat.example/login?code={code}")
        )
        bot.send_message = AsyncMock()
        bot.archive_message = AsyncMock()
        register_handlers(bot, -1001)
        receive = bot.message_handlers[-1]["function"]
        with patch("handlers.menu.miniapp_menu", return_value=None):
            await receive(telegram_message("/start"))
        sent = bot.send_message.await_args
        self.assertIn(f"https://decanat.example/login?code={code}", sent.args[1])
        self.assertIn(f"<code>{code}</code>", sent.args[1])
        self.assertEqual(sent.kwargs["parse_mode"], "HTML")
        bot.archive_message.assert_not_awaited()
        await receive(telegram_message(DEAN_QUESTION))
        bot.archive_message.assert_not_awaited()
        try:
            with patch("handlers.requests.send_question", new_callable=AsyncMock):
                await receive(telegram_message("Когда приём?"))
                await asyncio.gather(*bot.request_tasks.values())
        finally:
            clear_state((123, 123))

    async def test_failed_ai_is_reported_to_student_and_staff_and_does_not_remove_button(self):
        bot = SimpleNamespace(
            token="123:test",
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=42)),
            reply_to=AsyncMock(),
            write_log=Mock(),
            log_error=Mock(),
        )
        with patch("handlers.requests.answer_question_async", side_effect=RuntimeError("unavailable")):
            await send_question(bot, telegram_message("Когда приём?"), -1001)
        original, notification = bot.send_message.await_args_list
        self.assertIsNotNone(original.kwargs["reply_markup"])
        self.assertEqual(notification.kwargs["reply_to_message_id"], 42)
        self.assertIn("AI-помощник не смог", notification.args[1])
        self.assertIn("Обратись в деканат", bot.reply_to.await_args.args[1])

    async def test_start_code_is_not_copied_into_logs(self):
        bot = Bot("123:test", -999)
        code = "sensitiveLoginCode0123456789012345"
        token = current_message.set(telegram_message("/start"))
        try:
            bot.log_message(
                "Бот → чат", telegram_message(f"https://decanat.example/login?code={code}\n{code}")
            )
        finally:
            current_message.reset(token)
        self.assertNotIn(code, bot.log_queue.get_nowait())
