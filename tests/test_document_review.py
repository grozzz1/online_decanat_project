import unittest
from unittest.mock import patch

from telebot.types import User

from tests import test_document_integration as integration
from tests.fixtures import TEXTS, confirmed_visuals, encoded, page
from utils.document_classification import classify_document
from utils.document_fields import extract_document_fields
from utils.document_rules import answer_question, preliminary_check
from utils.ocr import reprocess_document


class ClassificationTests(unittest.TestCase):
    def test_all_titles(self):
        titles = {
            "study_certificate": "СПРАВКА ОБ ОБУЧЕНИИ",
            "internship_certificate": "СПРАВКА О ПРАКТИКЕ",
            "academic_leave_application": "ЗАЯВЛЕНИЕ НА АКАДЕМИЧЕСКИЙ ОТПУСК",
        }
        for kind, title in titles.items():
            with self.subTest(kind=kind):
                result = classify_document(title)
                self.assertEqual(result["detected_document_type"], kind)
                self.assertEqual(result["document_title"], title)

    def test_unknown_and_conflicting_headings(self):
        self.assertEqual(
            classify_document("Номер 123. Дата выдачи 01.09.2026")["detected_document_type"], "unknown"
        )
        self.assertEqual(
            classify_document("СПРАВКА ОБ ОБУЧЕНИИ\nСПРАВКА О ПРАКТИКЕ")["detected_document_type"], "unknown"
        )

    def test_title_from_coordinate_ocr(self):
        words = [
            {"text": token, "line": [1, 1, 1], "bbox": [index * 50, 0, index * 50 + 40, 25]}
            for index, token in enumerate(("СПРАВКА", "ОБ", "ОБУЧЕНИИ"))
        ]
        result = classify_document("Основной OCR пропустил заголовок", [{"ocr_words": words}])
        self.assertEqual(result["detected_document_type"], "study_certificate")

    def test_mismatch_uses_actual_schema_and_preserves_raw_text(self):
        text = "СПРАВКА ОБ ОБУЧЕНИИ\n" + TEXTS["study_certificate"]
        result = reprocess_document(text, confirmed_visuals(), "academic_leave_application")
        self.assertEqual(result["document_type"], "study_certificate")
        self.assertFalse(result["document_type_matches_selection"])
        self.assertEqual(result["selected_document_type"], "academic_leave_application")
        self.assertEqual(result["ocr_text"], text)
        self.assertIn("university", result["fields"])
        self.assertNotIn("leave_reason", result["fields"])

    def test_unknown_never_copies_the_selection(self):
        result = reprocess_document("нечитаемый документ", confirmed_visuals(), "academic_leave_application")
        self.assertEqual(result["detected_document_type"], "unknown")
        self.assertIsNone(result["document_type_matches_selection"])

    def test_alternative_labels_and_absent_lines(self):
        text = "ФИО студента: Тестов Иван Андреевич\nНазвание вуза: Учебная академия «Пример»\nНачало периода обучения: 01.09.2022\nОкончание периода обучения: 31.08.2026"
        result = extract_document_fields(text, "study_certificate")
        self.assertEqual(result["fields"]["university"], "Учебная академия «Пример»")
        self.assertEqual(result["fields"]["study_start"], "2022-09-01")
        self.assertEqual(result["fields"]["study_end"], "2026-08-31")
        blank = extract_document_fields(
            "ФИО студента:\nНазвание вуза: Учебная академия\nНачало периода обучения:\nОкончание периода обучения: 31.08.2026",
            "study_certificate",
        )
        self.assertIsNone(blank["fields"]["student_name"])
        self.assertEqual(blank["field_statuses"]["student_name"], "missing")
        self.assertIsNone(blank["fields"]["study_start"])

    def test_rule_sources_and_no_stamp_requirement_for_leave(self):
        result = extract_document_fields(
            TEXTS["academic_leave_application"], "academic_leave_application", confirmed_visuals()
        )
        check = preliminary_check(result)
        self.assertEqual(check["status"], "document_ok")
        self.assertEqual(check["sources"], ["rule:4.1", "rule:4.2"])
        self.assertNotIn("stamp", check["required_fields"])
        result["fields"]["student_signature"] = False
        result["field_statuses"]["student_signature"] = "missing"
        self.assertEqual(preliminary_check(result)["sources"], ["rule:4.2"])

    def test_unknown_rules_and_unanswerable_questions(self):
        extraction = extract_document_fields(TEXTS["study_certificate"], "study_certificate")
        self.assertEqual(preliminary_check(extraction)["status"], "unknown")
        for question in (
            "Какой размер стипендии положен студенту?",
            "На какой курс переведут студента?",
            "Какова стоимость обучения?",
        ):
            self.assertFalse(
                answer_question({"question_id": "q", "question": question}, extraction)["answerable"]
            )
        extraction = extract_document_fields(TEXTS["internship_certificate"], "internship_certificate")
        answer = answer_question(
            {"question_id": "q", "question": "Укажи значение поля «ФИО руководителя практики»."}, extraction
        )
        self.assertEqual(answer["value"], "Смирнов Алексей Николаевич")


class ReviewRouteTests(unittest.TestCase):
    # Reuse setup methods, without inheriting and rerunning the existing test cases.
    setUp = integration.WebIntegrationTests.setUp
    tearDown = integration.WebIntegrationTests.tearDown
    finish_processing = integration.WebIntegrationTests.finish_processing
    upload = integration.WebIntegrationTests.upload

    def submit_mismatch(self):
        original = encoded(page()[0])
        with patch(
            "utils.ocr.pytesseract.image_to_string",
            return_value="СПРАВКА ОБ ОБУЧЕНИИ\n" + TEXTS["study_certificate"],
        ):
            response = self.upload(original, "document.png", "academic_leave_application")
            self.finish_processing()
        return response.json["request_id"], original

    def test_mismatch_stops_delivery_and_retyping_reuses_ocr(self):
        request_id, original = self.submit_mismatch()
        item = self.client.get("/api/messages").json["requests"][-1]
        self.assertEqual(item["status"], "needs_review")
        self.assertEqual(item["document_title"], "СПРАВКА ОБ ОБУЧЕНИИ")
        self.assertFalse(item["document_type_matches_selection"])
        self.bot.send_photo.assert_not_awaited()
        self.assertEqual(
            self.client.post(
                f"/api/requests/{request_id}/confirm", headers={"X-CSRF-Token": self.csrf}
            ).status_code,
            409,
        )
        with patch(
            "utils.ocr.pytesseract.image_to_string", side_effect=AssertionError("OCR must not repeat")
        ):
            response = self.client.post(
                f"/api/requests/{request_id}/type",
                json={
                    "document_type": "study_certificate",
                    "mode": "application_question",
                    "text": "Все ли поля заполнены?",
                },
                headers={"X-CSRF-Token": self.csrf},
            )
            self.assertEqual(response.status_code, 200)
            item = self.client.get("/api/messages").json["requests"][-1]
            self.assertTrue(item["document_type_matches_selection"])
            self.assertEqual(item["status"], "needs_review")
            self.assertEqual(self.client.get(f"/api/requests/{request_id}/photo").data, original)
            self.bot.send_photo.assert_not_awaited()
            response = self.client.post(
                f"/api/requests/{request_id}/confirm", headers={"X-CSRF-Token": self.csrf}
            )
            self.assertEqual(response.status_code, 202)
            self.finish_processing()
        self.assertEqual(self.client.get("/api/messages").json["requests"][-1]["status"], "sent")
        self.bot.send_photo.assert_awaited_once()
        self.assertEqual(
            self.client.post(
                f"/api/requests/{request_id}/confirm", headers={"X-CSRF-Token": self.csrf}
            ).status_code,
            409,
        )

    def test_unknown_sends_without_mismatch_confirmation(self):
        with patch("utils.ocr.pytesseract.image_to_string", return_value="непонятный текст"):
            response = self.upload(encoded(page()[0]), "unknown.png")
            self.finish_processing()
        self.assertEqual(response.status_code, 202)
        item = self.client.get("/api/messages").json["requests"][-1]
        self.assertEqual(item["detected_document_type"], "unknown")
        self.assertIsNone(item["document_type_matches_selection"])
        self.assertEqual(item["status"], "sent")
        self.assertFalse(item["type_mismatch_confirmed"])
        self.bot.send_photo.assert_awaited_once()

    def test_override_sends_original_selection_once_and_keeps_validation(self):
        request_id, _ = self.submit_mismatch()
        url = f"/api/requests/{request_id}/confirm"
        for invalid in ("true", 1, [], {}):
            self.assertEqual(
                self.client.post(
                    url, json={"type_mismatch_confirmed": invalid}, headers={"X-CSRF-Token": self.csrf}
                ).status_code,
                400,
            )
        with patch("utils.ocr.pytesseract.image_to_string", side_effect=AssertionError("OCR repeated")):
            self.assertEqual(
                self.client.post(
                    url, json={"type_mismatch_confirmed": True}, headers={"X-CSRF-Token": self.csrf}
                ).status_code,
                202,
            )
            self.assertEqual(
                self.client.post(
                    url, json={"type_mismatch_confirmed": True}, headers={"X-CSRF-Token": self.csrf}
                ).status_code,
                409,
            )
            self.finish_processing()
        item = self.client.get("/api/messages").json["requests"][-1]
        self.assertEqual(item["status"], "sent")
        self.assertEqual(item["selected_document_type"], "academic_leave_application")
        self.assertEqual(item["detected_document_type"], "study_certificate")
        self.assertEqual(item["document_type"], "academic_leave_application")
        self.assertTrue(item["type_mismatch_confirmed"])
        self.assertNotEqual(item["validation"]["completeness"]["status"], "document_ok")
        self.bot.send_photo.assert_awaited_once()
        self.assertIn("Заявление на академический отпуск", self.bot.send_photo.call_args.kwargs["caption"])

    def test_selecting_another_wrong_type_requires_new_explicit_override(self):
        request_id, original = self.submit_mismatch()
        with patch("utils.ocr.pytesseract.image_to_string", side_effect=AssertionError("OCR repeated")):
            response = self.client.post(
                f"/api/requests/{request_id}/type",
                json={"document_type": "internship_certificate", "mode": "application", "text": ""},
                headers={"X-CSRF-Token": self.csrf},
            )
        self.assertEqual(response.status_code, 200)
        item = self.client.get("/api/messages").json["requests"][-1]
        self.assertFalse(item["document_type_matches_selection"])
        self.assertFalse(item["type_mismatch_confirmed"])
        self.assertEqual(self.client.get(f"/api/requests/{request_id}/photo").data, original)
        self.assertEqual(
            self.client.post(
                f"/api/requests/{request_id}/confirm", headers={"X-CSRF-Token": self.csrf}
            ).status_code,
            409,
        )
        self.bot.send_photo.assert_not_awaited()

    def test_explicit_delivery_rejection_can_retry_without_sending_file_twice(self):
        from telebot.asyncio_helper import ApiTelegramException

        request_id, _ = self.submit_mismatch()
        # Long OCR produces text chunks after the file. Reject the first chunk.
        item = self.service.store.get_request(123, request_id)
        self.service.store.update_request(request_id, ocr_text=item["ocr_text"] + "\n" + "x" * 2000)
        self.bot.send_message.side_effect = ApiTelegramException(
            "sendMessage", None, {"error_code": 429, "description": "retry later"}
        )
        url = f"/api/requests/{request_id}/confirm"
        self.client.post(url, json={"type_mismatch_confirmed": True}, headers={"X-CSRF-Token": self.csrf})
        self.finish_processing()
        item = self.client.get("/api/messages").json["requests"][-1]
        self.assertEqual(item["status"], "failed")
        self.assertTrue(item["retry_allowed"])
        self.bot.send_photo.assert_awaited_once()
        self.bot.send_message.side_effect = None
        self.assertEqual(self.client.post(url, headers={"X-CSRF-Token": self.csrf}).status_code, 202)
        self.finish_processing()
        self.assertEqual(self.client.get("/api/messages").json["requests"][-1]["status"], "sent")
        self.bot.send_photo.assert_awaited_once()

    def test_lost_transport_response_does_not_offer_unsafe_retry(self):
        request_id, _ = self.submit_mismatch()
        self.bot.send_photo.side_effect = TimeoutError("response lost")
        url = f"/api/requests/{request_id}/confirm"
        self.client.post(url, json={"type_mismatch_confirmed": True}, headers={"X-CSRF-Token": self.csrf})
        self.finish_processing()
        item = self.client.get("/api/messages").json["requests"][-1]
        self.assertEqual(item["status"], "failed")
        self.assertFalse(item["retry_allowed"])
        self.assertEqual(self.client.post(url, headers={"X-CSRF-Token": self.csrf}).status_code, 409)

    def test_review_routes_keep_session_csrf_and_owner_checks(self):
        request_id, _ = self.submit_mismatch()
        for endpoint in ("type", "confirm"):
            self.assertEqual(self.client.post(f"/api/requests/{request_id}/{endpoint}").status_code, 403)
            self.assertEqual(
                self.app.test_client().post(f"/api/requests/{request_id}/{endpoint}").status_code, 401
            )
        self.assertEqual(
            self.client.post(
                f"/api/requests/{request_id}/type",
                json={"document_type": "invented", "mode": "application"},
                headers={"X-CSRF-Token": self.csrf},
            ).status_code,
            409,
        )
        other = self.app.test_client()
        code = self.service.store.create_login(User(456, False, "Other"))
        other.get(f"/login?code={code}")
        other_csrf = other.get("/api/session").json["csrf"]
        for endpoint in ("type", "confirm"):
            response = other.post(
                f"/api/requests/{request_id}/{endpoint}",
                json={"document_type": "study_certificate", "mode": "application_question", "text": "Вопрос"},
                headers={"X-CSRF-Token": other_csrf},
            )
            self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
