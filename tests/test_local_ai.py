"""Adversarial source checks; unit tests never need a model or external network."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests import test_document_integration as integration
from tests.fixtures import TEXTS, confirmed_visuals, encoded, page
from utils.document_fields import extract_document_fields
from utils.hybrid_extraction import supplement_fields
from utils.knowledge import ROOT, KnowledgeIndex, split_clauses
from utils.local_ai import AIConfig, AIError, LocalAI, strict_json
from utils.question_answering import QuestionEngine, refusal, validate_generated_answer


def extraction(key="study_certificate", text=None, visuals=True):
    return extract_document_fields(
        text if text is not None else TEXTS[key], key, confirmed_visuals() if visuals else None
    )


def fake_client(value=None, error=None):
    return Mock(config=AIConfig(enabled=True), generate=Mock(side_effect=error, return_value=value))


def remove_field(text, label):
    return "\n".join(line for line in text.splitlines() if not line.startswith(label))


class HybridTests(unittest.TestCase):
    def test_complete_document_and_visual_unknowns_do_not_call_text_model(self):
        client = fake_client()
        result = supplement_fields(extraction(visuals=False), TEXTS["study_certificate"], client)
        client.generate.assert_not_called()
        self.assertEqual(result["hybrid"]["status"], "not_needed")
        self.assertIsNone(result["fields"]["signature"])

    def test_missing_university_recovered_from_unusual_prose_with_provenance(self):
        text = remove_field(TEXTS["study_certificate"], "ВУЗ:")
        text = remove_field(text, "Университет:")
        text = remove_field(text, "Название вуза:")
        text += "\nМесто учёбы — Учебная академия «Горизонт»."
        baseline = extraction(text=text)
        self.assertIsNone(baseline["fields"]["university"])
        quote = text.splitlines()[-1]
        client = fake_client({"university": {"value": "Учебная академия «Горизонт»", "quote": quote}})
        result = supplement_fields(baseline, text, client)
        self.assertEqual(result["fields"]["university"], "Учебная академия «Горизонт»")
        self.assertEqual(result["field_sources"]["university"], "qwen")
        self.assertEqual(result["field_sources"]["signature"], "cv")
        self.assertEqual(result["field_sources"]["student_name"], "regex")
        self.assertEqual(result["fields"]["student_name"], baseline["fields"]["student_name"])
        self.assertEqual(client.generate.call_args.args[1]["MISSING_FIELDS"], ["university"])

    def test_hallucinated_quote_and_unknown_keys_rejected(self):
        text = remove_field(TEXTS["study_certificate"], "Университет:")
        baseline = extraction(text=text)
        # Force a genuinely absent field independently of the fixture's label spelling.
        baseline["fields"]["university"] = None
        baseline["field_statuses"]["university"] = "missing"
        for response in (
            {"university": {"value": "Придуманный вуз", "quote": "ВУЗ: Придуманный вуз"}},
            {"university": None, "student_name": "Подмена"},
        ):
            result = supplement_fields(baseline, text, fake_client(response))
            self.assertIsNone(result["fields"]["university"])
            self.assertEqual(result["fields"]["student_name"], baseline["fields"]["student_name"])

    def test_date_cannot_be_borrowed_from_issue_date(self):
        text = "Дата выдачи: 01.09.2026"
        baseline = extraction()
        baseline["fields"]["study_start"] = None
        baseline["field_statuses"]["study_start"] = "missing"
        result = supplement_fields(
            baseline, text, fake_client({"study_start": {"value": "2026-09-01", "quote": text}})
        )
        self.assertIsNone(result["fields"]["study_start"])
        self.assertIn("study_start", result["hybrid"]["rejected"])

    def test_invalid_date_and_string_null_are_not_accepted(self):
        baseline = extraction()
        baseline["fields"]["study_start"] = None
        baseline["field_statuses"]["study_start"] = "unknown"
        for value in ("2026-02-31", "null"):
            result = supplement_fields(
                baseline,
                "Начало обучения: 31.02.2026",
                fake_client({"study_start": {"value": value, "quote": "Начало обучения: 31.02.2026"}}),
            )
            self.assertIsNone(result["fields"]["study_start"])

    def test_ambiguous_name_is_not_overridden(self):
        baseline = extraction()
        baseline["fields"]["student_name"] = None
        baseline["field_statuses"]["student_name"] = "ambiguous"
        quote = "ФИО студента: Иванов Иван Иванович"
        result = supplement_fields(
            baseline, quote, fake_client({"student_name": {"value": "Иванов Иван Иванович", "quote": quote}})
        )
        self.assertIsNone(result["fields"]["student_name"])
        self.assertEqual(result["hybrid"]["rejected"]["student_name"], "ambiguous_regex_candidates")

    def test_model_unavailable_keeps_regex_values_and_visuals(self):
        baseline = extraction()
        baseline["fields"]["university"] = None
        baseline["field_statuses"]["university"] = "missing"
        result = supplement_fields(baseline, "OCR", fake_client(error=AIError("transport_unavailable")))
        self.assertEqual(result["fields"], baseline["fields"])
        self.assertEqual(result["hybrid"]["status"], "transport_unavailable")

    def test_negated_university_statement_is_not_a_field_value(self):
        baseline = extraction()
        baseline["fields"]["university"] = None
        baseline["field_statuses"]["university"] = "missing"
        quote = "Студент не обучается в Учебной академии «Пример»."
        result = supplement_fields(
            baseline,
            quote,
            fake_client({"university": {"value": "Учебной академии «Пример»", "quote": quote}}),
        )
        self.assertIsNone(result["fields"]["university"])

    def test_period_pair_positions_and_reversed_pair(self):
        baseline = extraction()
        for field in ("study_start", "study_end"):
            baseline["fields"][field] = None
            baseline["field_statuses"][field] = "missing"
        quote = "Период обучения с 01.09.2022 по 31.08.2026"
        result = supplement_fields(
            baseline,
            quote,
            fake_client(
                {
                    "study_start": {"value": "2022-09-01", "quote": quote},
                    "study_end": {"value": "2026-08-31", "quote": quote},
                }
            ),
        )
        self.assertEqual(result["fields"]["study_start"], "2022-09-01")
        reversed_result = supplement_fields(
            baseline,
            quote,
            fake_client(
                {
                    "study_start": {"value": "2026-08-31", "quote": quote},
                    "study_end": {"value": "2022-09-01", "quote": quote},
                }
            ),
        )
        self.assertIsNone(reversed_result["fields"]["study_start"])
        self.assertIsNone(reversed_result["fields"]["study_end"])


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.index = KnowledgeIndex(index_path=Path(self.directory.name) / "index.json")

    def test_rules_keep_all_clause_ids_and_exact_text(self):
        chunks = split_clauses(ROOT / "rules.md", (ROOT / "rules.md").read_text(encoding="utf-8"))
        sources = [chunk.source for chunk in chunks if chunk.source.startswith("rule:")]
        self.assertEqual(len(sources), 30)
        self.assertEqual(len(set(sources)), 30)
        self.assertEqual(self.index.get("rule:1.3").text.split()[0], "1.3.")

    def test_search_and_no_matches(self):
        hits = self.index.search("Кто принимает окончательное решение о принятии документа?", 3)
        self.assertEqual(hits[0]["source"], "rule:1.3")
        self.assertEqual(self.index.search("абракадабраxyz987"), [])
        self.assertIsNone(self.index.get("rule:99.99"))

    def test_index_reused_and_rebuilt_after_change(self):
        file = Path(self.directory.name) / "additional.txt"
        file.write_text("1.1. Ответственный сотрудник принимает решение.", encoding="utf-8")
        index = KnowledgeIndex([file], Path(self.directory.name) / "extra.json")
        index.search("решение")
        index.search("решение")
        self.assertEqual(index.builds, 1)
        file.write_text(
            "1.1. Ответственный сотрудник принимает решение.\n1.2. Студент получает ответ.", encoding="utf-8"
        )
        index.search("студент")
        self.assertEqual(index.builds, 2)
        loaded = KnowledgeIndex([file], Path(self.directory.name) / "extra.json")
        loaded.search("решение")
        self.assertEqual(loaded.builds, 0)


class QuestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.index = KnowledgeIndex(index_path=Path(self.temp.name) / "index.json")
        self.client = fake_client()
        self.engine = QuestionEngine(self.client, self.index)

    def ask(self, text, fields=None):
        return self.engine.answer({"question_id": "question-1", "question": text}, fields)

    def test_field_question_and_missing_field_never_call_llm(self):
        fields = extraction()
        result = self.ask("Какое название вуза указано в документе?", fields)
        self.assertEqual(result["result"]["value"], fields["fields"]["university"])
        fields["fields"]["university"] = None
        result = self.ask("Какое название вуза указано в документе?", fields)
        self.assertFalse(result["result"]["answerable"])
        self.assertEqual(result["result"]["sources"], [])
        self.client.generate.assert_not_called()

    def test_date_from_foreign_schema_is_not_replaced(self):
        result = self.ask("Какова дата выдачи?", extraction("academic_leave_application"))
        self.assertFalse(result["result"]["answerable"])
        self.client.generate.assert_not_called()

    def test_presence_absence_and_unknown_visuals(self):
        fields = extraction()
        fields["fields"]["signature"] = False
        fields["field_statuses"]["signature"] = "missing"
        result = self.ask("Есть ли подпись?", fields)
        self.assertTrue(result["result"]["answerable"])
        self.assertEqual(result["result"]["value"], "false")
        fields["fields"]["signature"] = None
        fields["field_statuses"]["signature"] = "unknown"
        self.assertFalse(self.ask("Есть ли подпись?", fields)["result"]["answerable"])

    def test_missing_visual_field_and_different_signature_role_never_use_llm(self):
        for text, kind in [
            ("Есть ли печать в заявлении?", "academic_leave_application"),
            ("Есть ли подпись сотрудника деканата?", "academic_leave_application"),
            ("Есть ли подпись студента?", "study_certificate"),
            ("Укажи значение поля «issue_date».", "academic_leave_application"),
        ]:
            self.assertFalse(self.ask(text, extraction(kind))["result"]["answerable"])
        self.client.generate.assert_not_called()

    def test_canonical_field_name_is_answered_without_generation(self):
        fields = extraction()
        result = self.ask("Укажи значение поля «university».", fields)
        self.assertEqual(result["result"]["value"], fields["fields"]["university"])
        self.client.generate.assert_not_called()

    def test_validation_and_decision_rules_without_llm(self):
        self.assertEqual(
            self.ask("Содержит ли документ все обязательные сведения?", extraction())["result"]["value"],
            "document_ok",
        )
        result = self.ask("Кто принимает окончательное решение о принятии документа?")
        self.assertEqual(result["result"]["sources"], ["rule:1.3"])
        self.client.generate.assert_not_called()

    def test_academic_leave_does_not_require_stamp(self):
        result = self.ask("Нужна ли печать по Положению?", extraction("academic_leave_application"))
        self.assertEqual(result["result"]["sources"], ["rule:4.3"])
        self.assertIn("не требуются", result["result"]["answer"])

    def test_rule_query_without_document_and_explicit_other_type(self):
        result = self.ask("Что нужно для справки об обучении по Положению?")
        self.assertEqual(result["result"]["sources"], ["rule:3.1", "rule:3.2", "rule:3.3"])
        result = self.ask("Нужна ли печать для заявления на академический отпуск?", extraction())
        self.assertEqual(result["result"]["sources"], ["rule:4.3"])
        self.client.generate.assert_not_called()

    def test_unanswerable_cost_and_transfer_course(self):
        for text in (
            "Какова стоимость обучения?",
            "На какой курс переведут студента?",
            "Какой срок рассмотрения документа?",
            "По какому номеру телефона связаться с деканатом?",
        ):
            self.assertFalse(self.ask(text, extraction("academic_leave_application"))["result"]["answerable"])
        self.client.generate.assert_not_called()

    def test_no_retrieval_matches_does_not_claim_definite_absence(self):
        result = self.ask("абракадабраxyz987")
        self.assertIn("Не найдены подходящие источники", result["result"]["answer"])
        self.assertEqual(result["diagnostics"]["reason"], "retrieval_no_matches")
        self.client.generate.assert_not_called()

    def test_complex_answer_contains_only_verified_complete_quotes(self):
        clause = self.index.get("rule:2.1")
        self.client.generate.return_value = {
            "question_id": "question-1",
            "answerable": True,
            "sources": [clause.source],
        }
        result = self.ask("Какие источники ответа разрешены сервису?", extraction())
        self.assertTrue(result["result"]["answerable"])
        self.assertEqual(result["result"]["value"], clause.text)

    def test_fabricated_rule_or_invalid_json_retries_once_then_safe_refusal(self):
        for response in ({"question_id": "question-1", "answerable": True, "sources": ["rule:999.1"]}, {}):
            self.client.generate.reset_mock()
            self.client.generate.return_value = response
            result = self.ask("Какие источники ответа разрешены сервису?", extraction())
            self.assertFalse(result["result"]["answerable"])
            self.assertEqual(self.client.generate.call_count, 2)
            self.assertTrue(result["diagnostics"]["technical_error"])

    def test_generated_values_sources_ids_and_string_null_checked(self):
        fields = extraction()
        question = {"question_id": "q", "question": "Какое название вуза?"}
        expected = {
            "question_id": "q",
            "answerable": True,
            "value": fields["fields"]["university"],
            "answer": "correct",
            "sources": ["field:university"],
        }
        for changes in (
            {"question_id": "other"},
            {"value": "null"},
            {"sources": ["field:invented"]},
            {"value": "Чужой вуз"},
            {"sources": ["rule:99.1"]},
            {"answerable": "true"},
        ):
            with self.assertRaises(AIError):
                validate_generated_answer({**expected, **changes}, question, fields, [], expected)
        checked = validate_generated_answer(
            {**expected, "answer": "Документ юридически принят!"}, question, fields, [], expected
        )
        self.assertEqual(checked["answer"], "correct")


class ClientTests(unittest.TestCase):
    def test_ai_field_values_are_not_copied_to_telegram_admin_log(self):
        from types import SimpleNamespace

        from utils.bot import Bot, current_ai_reply

        bot = SimpleNamespace(admin_log_chat_id=-100, get_context=Mock(return_value=""), write_log=Mock())
        message = SimpleNamespace(
            chat=SimpleNamespace(id=123),
            message_id=1,
            content_type="text",
            text="PRIVATE_FIELD_VALUE",
            caption=None,
            photo=None,
            document=None,
        )
        privacy = current_ai_reply.set(True)
        try:
            Bot.log_message(bot, "Бот → пользователь", message)
        finally:
            current_ai_reply.reset(privacy)
        self.assertNotIn("PRIVATE_FIELD_VALUE", bot.write_log.call_args.args[0])

    def test_transport_failure_opens_circuit_and_redacts_error(self):
        client = LocalAI(AIConfig())
        with patch.object(client, "_request", side_effect=OSError("private OCR contents")) as transport:
            with self.assertRaisesRegex(AIError, "transport_unavailable") as error:
                client.generate("system", {}, {"type": "object"})
            self.assertNotIn("private", str(error.exception))
            with self.assertRaisesRegex(AIError, "circuit_open"):
                client.generate("system", {}, {"type": "object"})
            self.assertEqual(transport.call_count, 1)

    def test_only_loopback_endpoint_allowed(self):
        for url in (
            "https://api.example.com",
            "http://127.0.0.1.evil.test",
            "http://user:pass@localhost",
            "http://localhost/remote",
        ):
            with self.assertRaises(ValueError):
                LocalAI(AIConfig(endpoint=url))

    def test_invalid_duplicate_and_nonfinite_json(self):
        for value in ('{"a":1,"a":2}', '{"x":NaN}', "```json\n{}\n```", "bad"):
            with self.assertRaises(AIError):
                strict_json(value)

    def test_model_parameters_and_bounded_queue(self):
        client = LocalAI(AIConfig(queue_timeout=0.01))
        schema = {"type": "object"}
        with patch.object(
            client,
            "_request",
            return_value={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]},
        ) as transport:
            self.assertEqual(client.generate("system", {"OCR": "private"}, schema), {})
            payload = transport.call_args.args[1]
            self.assertEqual(payload["temperature"], 0)
            self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])
            self.assertFalse(payload["stream"])
        client.gate.acquire()
        try:
            with self.assertRaisesRegex(AIError, "queue_timeout"):
                client.generate("system", {}, schema)
        finally:
            client.gate.release()


class AIWebTests(unittest.TestCase):
    setUp = integration.WebIntegrationTests.setUp
    tearDown = integration.WebIntegrationTests.tearDown
    finish_processing = integration.WebIntegrationTests.finish_processing
    upload = integration.WebIntegrationTests.upload

    def test_ask_route_owner_csrf_and_background_python_answer(self):
        response = self.upload(encoded(page()[0]), "document.png")
        self.finish_processing()
        request_id = response.json["request_id"]
        url = f"/api/requests/{request_id}/ask"
        self.assertEqual(self.client.post(url, json={"question": "Какой курс?"}).status_code, 403)
        self.assertEqual(self.app.test_client().post(url, json={"question": "Какой курс?"}).status_code, 401)
        self.assertEqual(
            self.client.post(
                url, json={"question": "Какой курс?"}, headers={"X-CSRF-Token": self.csrf}
            ).status_code,
            202,
        )
        self.finish_processing()
        item = self.client.get("/api/messages").json["requests"][-1]
        self.assertEqual(item["ai_status"], "done")
        self.assertEqual(item["ai_answer"]["result"]["sources"], ["field:course"])
        self.assertFalse(item["ai_answer"]["diagnostics"]["llm_called"])
        self.assertEqual(item["status"], "sent")

    def test_another_user_cannot_ask_about_foreign_document(self):
        from telebot.types import User

        response = self.upload(encoded(page()[0]), "document.png")
        self.finish_processing()
        request_id = response.json["request_id"]
        other = self.app.test_client()
        code = self.service.store.create_login(User(456, False, "Other"))
        other.get(f"/login?code={code}")
        csrf = other.get("/api/session").json["csrf"]
        result = other.post(
            f"/api/requests/{request_id}/ask",
            json={"question": "Какой курс?"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(result.status_code, 404)

    def test_slow_answer_runs_in_background_and_second_ask_is_rejected(self):
        from asyncio import Event, wait_for
        from threading import Thread

        response = self.upload(encoded(page()[0]), "document.png")
        self.finish_processing()
        request_id = response.json["request_id"]

        async def make_gate():
            return Event()

        gate = self.service.call(make_gate())

        async def slow_answer(*args):
            await wait_for(gate.wait(), timeout=5)
            return {"result": refusal("q"), "diagnostics": {"llm_called": True}}

        responses = []

        def post():
            responses.append(
                self.client.post(
                    f"/api/requests/{request_id}/ask",
                    json={"question": "Проверь источники"},
                    headers={"X-CSRF-Token": self.csrf},
                )
            )

        with patch("webapp.service.answer_question_async", new=slow_answer):
            worker = Thread(target=post)
            worker.start()
            worker.join(timeout=2)
            try:
                self.assertFalse(worker.is_alive(), "HTTP response waited for inference")
                self.assertEqual(responses[0].status_code, 202)
                repeated = self.client.post(
                    f"/api/requests/{request_id}/ask",
                    json={"question": "Ещё вопрос"},
                    headers={"X-CSRF-Token": self.csrf},
                )
                self.assertEqual(repeated.status_code, 409)
            finally:
                self.loop.call_soon_threadsafe(gate.set)
                worker.join(timeout=6)
                self.finish_processing()


if __name__ == "__main__":
    unittest.main()
