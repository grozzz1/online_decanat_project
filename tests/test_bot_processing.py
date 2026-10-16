import asyncio
import unittest
from io import BytesIO
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from handlers import register_handlers
from handlers.requests import accept_request
from tests import test_document_integration as integration
from tests.fixtures import TEXTS, confirmed_visuals, encoded, page
from tests.test_conversation_flow import telegram_message
from utils.bot import Bot, current_document, current_message
from utils.local_ai import AIConfig
from utils.ocr import analyze_document_async, reprocess_document
from utils.question_answering import answer_question_async
from utils.states import application_types, clear_state, states


def analysis():
    return reprocess_document(
        TEXTS["academic_leave_application"],
        confirmed_visuals(),
        "academic_leave_application",
        use_ai=False,
    )


async def wait_for_event(event):
    async with asyncio.timeout(3):
        while not event.is_set():
            await asyncio.sleep(0.005)


class DocumentDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_modes_reply_before_slow_or_failed_dean_delivery(self):
        for state in ("application_photo", "application_question"):
            with self.subTest(state=state):
                bot, message = integration.BotIntegrationTests.document_bot(self)
                if state == "application_question":
                    message.caption = "Кто принимает окончательное решение?"
                key = (123, 123)
                states[key] = state
                application_types[key] = "academic_leave_application"
                entered, release = asyncio.Event(), asyncio.Event()

                async def send_photo(*args, **kwargs):
                    entered.set()
                    await release.wait()
                    raise OSError("Gateway Timeout")

                bot.send_photo.side_effect = send_photo
                answer = {"result": {"answer": "Ответ проверен", "sources": [], "answerable": True}}
                try:
                    with (
                        patch("handlers.requests.analyze_document_async", return_value=analysis()),
                        patch("handlers.requests.answer_question_async", return_value=answer),
                    ):
                        task = asyncio.create_task(accept_request(bot, message, key, -1001))
                        try:
                            await asyncio.wait_for(entered.wait(), timeout=2)
                            for _ in range(10):
                                await asyncio.sleep(0)
                            replies = [call.args[1] for call in bot.reply_to.await_args_list]
                            self.assertTrue(any("Результат проверки:" in text for text in replies))
                            self.assertTrue(any("ФИО студента:" in text for text in replies))
                            if state == "application_question":
                                self.assertTrue(any("Ответ проверен" in text for text in replies))
                            self.assertFalse(task.done())
                            self.assertEqual(bot.send_photo.await_args.args[1], message.document.file_id)
                            self.assertEqual(bot.send_photo.await_args.kwargs["timeout"], 30)
                        finally:
                            release.set()
                            await task
                    replies = [call.args[1] for call in bot.reply_to.await_args_list]
                    self.assertTrue(any("Не удалось подтвердить доставку" in text for text in replies))
                    self.assertNotIn("Документ отправлен в деканат.", replies)
                    self.assertNotIn("Вопрос отправлен в деканат.", replies)
                    bot.log_error.assert_called_once()
                finally:
                    clear_state(key)

    async def test_pdf_reuses_telegram_file_id(self):
        bot, message = integration.BotIntegrationTests.document_bot(self)
        buffer = BytesIO()
        page()[0].save(buffer, "PDF")
        bot.download_file.return_value = buffer.getvalue()
        bot.send_document.return_value = SimpleNamespace(message_id=42)
        message.document.file_id = "cached-pdf"
        key = (123, 123)
        states[key] = "application_photo"
        application_types[key] = "academic_leave_application"
        try:
            with patch("handlers.requests.analyze_document_async", return_value=analysis()):
                await accept_request(bot, message, key, -1001)
            self.assertEqual(bot.send_document.await_args.args[1], "cached-pdf")
            self.assertTrue(
                any("Результат проверки:" in call.args[1] for call in bot.reply_to.await_args_list)
            )
            self.assertEqual(bot.reply_to.await_args.args[1], "Документ отправлен в деканат.")
        finally:
            clear_state(key)


class BackgroundProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_remains_available_and_navigation_does_not_change_pending_document(self):
        bot = Bot("123:test", -999)
        bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=42))
        bot.send_photo = AsyncMock(return_value=SimpleNamespace(message_id=43))
        bot.reply_to = AsyncMock()
        bot.get_file = AsyncMock(return_value=SimpleNamespace(file_path="document.png"))
        bot.download_file = AsyncMock(return_value=encoded(page()[0]))
        _, message = integration.BotIntegrationTests.document_bot(self)
        message.chat.type = "private"
        message.text = None
        register_handlers(bot, -1001)
        receive = bot.message_handlers[-1]["function"]
        entered, release = asyncio.Event(), asyncio.Event()

        async def analyze(*args):
            entered.set()
            await release.wait()
            return analysis()

        key = (123, 123)
        states[key] = "application_photo"
        application_types[key] = "academic_leave_application"
        with (
            patch("handlers.requests.analyze_document_async", side_effect=analyze) as document,
            patch("handlers.requests.answer_question_async", new_callable=AsyncMock) as ai,
            patch("handlers.menu.miniapp_menu", return_value=None),
        ):
            try:
                await asyncio.wait_for(receive(message), timeout=1)
                await asyncio.wait_for(entered.wait(), timeout=1)
                self.assertIn(key, bot.request_tasks)
                await asyncio.wait_for(receive(telegram_message("/start", 11)), timeout=1)
                self.assertIn("Выбери, с чем нужна помощь", bot.send_message.await_args.args[1])
                states[key] = "application_question"
                application_types[key] = "study_certificate"
                message.caption = "Новый вопрос"
                await asyncio.wait_for(receive(message), timeout=1)
                self.assertIn("ещё обрабатывается", bot.reply_to.await_args.args[1])
                self.assertEqual(document.await_count, 1)
            finally:
                release.set()
                await asyncio.gather(*bot.request_tasks.values())
                await asyncio.sleep(0)
                clear_state(key)
            self.assertFalse(bot.request_tasks)
            ai.assert_not_awaited()
            self.assertIsNone(bot.send_photo.await_args.kwargs["reply_markup"])
            self.assertTrue(
                any("Результат проверки:" in call.args[1] for call in bot.reply_to.await_args_list)
            )

    async def test_background_document_values_stay_out_of_logs_after_navigation(self):
        bot = Bot("123:test", -999)
        tokens = current_message.set(telegram_message("Документ")), current_document.set("Заявление")
        try:
            clear_state((123, 123))
            bot.log_message("Бот → чат", telegram_message("ФИО студента: Private Student Value"))
            self.assertNotIn("Private Student Value", bot.log_queue.get_nowait())
        finally:
            current_message.reset(tokens[0])
            current_document.reset(tokens[1])


class ProcessingThreadTests(unittest.IsolatedAsyncioTestCase):
    async def test_cv_and_llm_extraction_run_off_the_event_loop(self):
        loop_thread = get_ident()
        cv_entered, cv_release = Event(), Event()
        llm_entered, llm_release = Event(), Event()
        workers = []

        def cv(*args):
            workers.append(get_ident())
            cv_entered.set()
            cv_release.wait(timeout=5)
            return {"detections": []}

        def generate(prompt, payload, schema, **kwargs):
            workers.append(get_ident())
            llm_entered.set()
            llm_release.wait(timeout=5)
            return {name: None for name in payload["MISSING_FIELDS"]}

        client = SimpleNamespace(config=AIConfig(enabled=True), generate=Mock(side_effect=generate))
        with (
            patch("utils.ocr.pytesseract.image_to_osd", return_value={}),
            patch("utils.ocr.pytesseract.image_to_string", return_value="ЗАЯВЛЕНИЕ НА АКАДЕМИЧЕСКИЙ ОТПУСК"),
            patch("utils.ocr._ocr_words", return_value=[]),
            patch("utils.ocr.analyze_document_visuals", side_effect=cv),
            patch("utils.hybrid_extraction.get_local_ai", return_value=client),
        ):
            task = asyncio.create_task(
                analyze_document_async(BytesIO(encoded(page()[0])), "academic_leave_application")
            )
            try:
                await wait_for_event(cv_entered)
                self.assertFalse(task.done())
                self.assertNotEqual(workers[-1], loop_thread)
                cv_release.set()
                await wait_for_event(llm_entered)
                self.assertFalse(task.done())
                self.assertNotEqual(workers[-1], loop_thread)
            finally:
                cv_release.set()
                llm_release.set()
                await task
        self.assertEqual(len(workers), 2)

    async def test_qa_model_does_not_block_the_event_loop(self):
        entered, release = Event(), Event()
        workers = []

        def answer(*args):
            workers.append(get_ident())
            entered.set()
            release.wait(timeout=5)
            return {"result": "done"}

        with patch(
            "utils.question_answering.get_question_engine", return_value=SimpleNamespace(answer=answer)
        ):
            task = asyncio.create_task(answer_question_async({"question_id": "1", "question": "Вопрос"}))
            try:
                await wait_for_event(entered)
                self.assertFalse(task.done())
                self.assertNotEqual(workers[0], get_ident())
            finally:
                release.set()
                self.assertEqual(await task, {"result": "done"})
