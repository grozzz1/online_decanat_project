import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telebot.types import Message

from handlers import register_handlers, types
from tests import test_document_integration
from utils.bot import Bot


class TypeCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = test_document_integration.WebIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.bot = Bot("123:test", -999)
        self.bot.web = self.fixture.service
        self.bot.reply_to = AsyncMock()
        self.bot.send_message = AsyncMock()
        self.bot.answer_callback_query = AsyncMock()
        self.bot.edit_message_text = AsyncMock()
        self.bot.edit_message_reply_markup = AsyncMock()
        register_handlers(self.bot, -1001)

    def message(self, text, chat_id=-1001):
        return Message.de_json(
            {
                "message_id": 1,
                "date": 0,
                "chat": {"id": chat_id, "type": "supergroup" if chat_id < 0 else "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "Staff"},
                "text": text,
            }
        )

    async def test_dean_adds_type_chooses_modes_and_removes_only_custom_type(self):
        message = self.message("/add_type Заявление на перевод")
        await self.bot.process_new_messages([message])
        markup = self.bot.reply_to.call_args.kwargs["reply_markup"].to_dict()
        data = markup["inline_keyboard"][-1][0]["callback_data"]
        call = SimpleNamespace(id="callback", message=message, from_user=message.from_user, data=data)
        await types.callback(self.bot, call)
        registry = self.fixture.service.store.types
        entry = registry.resolve("Заявление на перевод")
        self.assertEqual(entry["modes"], ["application", "application_question"])
        call.data = "doctype:remove:" + entry["key"]
        await types.callback(self.bot, call)
        self.assertIsNone(registry.resolve(entry["key"]))
        await self.bot.process_new_messages([self.message("/remove_type academic_leave_application")])
        self.assertIn("нельзя удалить", self.bot.reply_to.call_args.args[1])
        self.assertIsNotNone(registry.resolve("academic_leave_application"))

    async def test_private_command_does_not_change_catalog(self):
        await self.bot.process_new_messages([self.message("/add_type Недопустимое добавление", 42)])
        self.assertIsNone(self.fixture.service.store.types.resolve("Недопустимое добавление"))

    async def test_other_staff_cannot_confirm_someone_elses_pending_creation(self):
        message = self.message("/add_type Справка для общежития")
        await types.command(self.bot, message)
        data = self.bot.reply_to.call_args.kwargs["reply_markup"].to_dict()["inline_keyboard"][0][0][
            "callback_data"
        ]
        call = SimpleNamespace(id="callback", message=message, from_user=SimpleNamespace(id=43), data=data)
        with self.assertRaises(ValueError):
            await types.callback(self.bot, call)
