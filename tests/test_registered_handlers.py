import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telebot.types import Message

from handlers import locks, register_handlers
from utils.bot import Bot
from utils.keyboards import DEAN_QUESTION
from utils.states import clear_state, states


class RegisteredHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_start_and_question_through_registered_handlers(self):
        bot = Bot("123:test", -999)
        bot.send_message = AsyncMock()
        bot.reply_to = AsyncMock()
        bot.log_message = Mock()
        bot.log_error = Mock()
        bot.web = SimpleNamespace(
            create_login=AsyncMock(return_value="http://localhost:5000/login?code=TEST")
        )
        register_handlers(bot, -100)
        key = (123, 123)

        def message(text, number):
            return Message.de_json(
                {
                    "message_id": number,
                    "date": 0,
                    "chat": {"id": 123, "type": "private"},
                    "from": {"id": 123, "is_bot": False, "first_name": "Synthetic"},
                    "text": text,
                }
            )

        try:
            await bot.process_new_messages([message("/start", 1)])
            self.assertIn("login?code=TEST", bot.send_message.call_args.args[1])
            await bot.process_new_messages([message(DEAN_QUESTION, 2)])
            self.assertEqual(states[key], "dean_question")
            with patch("handlers.requests.send_question", new_callable=AsyncMock) as send:
                await bot.process_new_messages([message("Кто принимает окончательное решение?", 3)])
                send.assert_awaited_once()
            self.assertNotIn(key, states)
            bot.log_error.assert_not_called()
        finally:
            clear_state(key)
            locks.pop(key, None)
