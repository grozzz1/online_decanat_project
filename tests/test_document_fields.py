import logging
import unittest

from utils.document_fields import DOCUMENT_SCHEMAS, extract_document_fields, normalize_date, normalize_document_type
from utils.document_logging import extraction_counts, log_document_failure, log_document_result
from utils.validation import validate_document_fields


TEXTS = {
    "study_certificate": """Справка № 45/26 от 02.09.2026
ФИО студента: Иванов Иван Иванович
ВУЗ: Московский технический университет
Направление подготовки: 09.03.01 Информатика и вычислительная техника
Форма обучения: очная
Период обучения с 01.09.2024 по 30.06.2028
Подпись: __________ Печать
""",
    "academic_leave_application": """ФИО: Петров Петр Петрович
Группа: ИКБО-01-24
Курс: 2
Причина отпуска: по семейным обстоятельствам
Прошу предоставить академический отпуск с 15.10.2026 по 15.10.2027
Дата заявления: 14.10.2026
Подпись студента: __________
""",
    "internship_certificate": """Номер документа: ПР-123
Дата выдачи: 20.08.2026
Студент: Сидорова Анна Сергеевна
Организация: ООО «Технологии»
Прошла практику с 01.07.2026 по 15.08.2026
Руководитель практики: Смирнов Алексей Николаевич
Оценка: отлично
Подпись: _________ Печать
""",
}


def confirmed_visuals():
    found = {"detected": True, "status": "detected", "confidence": .8, "role": "student"}
    return {"stamp": dict(found), "signature": dict(found), "student_signature": dict(found),
            "pages": [{"analysis_status": "complete"}], "warnings": [], "detections": []}


class ExtractionTests(unittest.TestCase):
    def test_all_fields_for_all_document_types(self):
        expected = {
            "study_certificate": {"student_name": "Иванов Иван Иванович", "university": "Московский технический университет",
                "program": "09.03.01 Информатика и вычислительная техника", "study_form": "очная",
                "study_start": "2024-09-01", "study_end": "2028-06-30", "document_number": "45/26", "issue_date": "2026-09-02", "signature": True, "stamp": True},
            "academic_leave_application": {"student_name": "Петров Петр Петрович", "group": "ИКБО-01-24", "course": 2,
                "leave_reason": "по семейным обстоятельствам", "leave_start": "2026-10-15", "leave_end": "2027-10-15",
                "application_date": "2026-10-14", "student_signature": True},
            "internship_certificate": {"student_name": "Сидорова Анна Сергеевна", "organization": "ООО «Технологии»",
                "internship_start": "2026-07-01", "internship_end": "2026-08-15", "supervisor_name": "Смирнов Алексей Николаевич",
                "grade": "отлично", "document_number": "ПР-123", "issue_date": "2026-08-20", "signature": True, "stamp": True},
        }
        for key, text in TEXTS.items():
            with self.subTest(document_type=key):
                result = extract_document_fields(text, key, confirmed_visuals())
                self.assertEqual(result["fields"], expected[key])
                self.assertEqual(set(result["field_statuses"].values()), {"found"})
                self.assertEqual(set(result["fields"]), set(DOCUMENT_SCHEMAS[key]["fields"]))

    def test_empty_fields_are_null_and_visuals_are_unknown(self):
        for key in DOCUMENT_SCHEMAS:
            with self.subTest(key=key):
                result = extract_document_fields("", key)
                self.assertTrue(all(value is None for value in result["fields"].values()))
                self.assertEqual(extraction_counts(result)["found"], 0)
                self.assertEqual(result["field_statuses"]["student_signature" if key == "academic_leave_application" else "stamp"], "unknown")

    def test_text_labels_do_not_prove_visual_presence(self):
        result = extract_document_fields("Подпись студента: имеется\nПечать: имеется", "academic_leave_application")
        self.assertIsNone(result["fields"]["student_signature"])
        result = extract_document_fields("Подпись: есть\nПечать: есть", "study_certificate")
        self.assertIsNone(result["fields"]["signature"])
        self.assertIsNone(result["fields"]["stamp"])

    def test_uncertain_failed_and_staff_signatures(self):
        for change in ({"status": "uncertain", "confidence": .6}, {"status": "unknown", "detected": False}, {"role": "unknown"}):
            visuals = confirmed_visuals()
            visuals["student_signature"].update(change)
            result = extract_document_fields("", "academic_leave_application", visuals)
            self.assertIsNone(result["fields"]["student_signature"])

    def test_negative_visuals_require_completed_analysis(self):
        visuals = confirmed_visuals()
        visuals["stamp"] = {"detected": False, "status": "not_detected"}
        self.assertIs(extract_document_fields("", "study_certificate", visuals)["fields"]["stamp"], False)
        visuals["pages"][0]["analysis_status"] = "unavailable"
        self.assertIsNone(extract_document_fields("", "study_certificate", visuals)["fields"]["stamp"])

    def test_ambiguous_names_and_dates_are_not_chosen(self):
        text = "ФИО: Иванов Иван Иванович\nФИО: Петров Петр Петрович\nДата начала отпуска: 01.09.2026\nДата начала отпуска: 02.09.2026"
        result = extract_document_fields(text, "academic_leave_application")
        for key in ("student_name", "leave_start"):
            self.assertIsNone(result["fields"][key])
            self.assertEqual(result["field_statuses"][key], "ambiguous")

    def test_same_value_repeated_is_unambiguous(self):
        result = extract_document_fields("Дата выдачи: 01.09.2026\nДата выдачи: 2026-09-01", "study_certificate")
        self.assertEqual(result["fields"]["issue_date"], "2026-09-01")

    def test_dates_do_not_cross_document_periods(self):
        text = "Период обучения с 01.09.2024 по 30.06.2028\nПрошу отпуск с 01.10.2026 по 01.10.2027\nПрактика с 01.07.2026 по 01.08.2026\nДата рождения: 20.01.2004"
        expectations = {"study_certificate": ("study_start", "2024-09-01"), "academic_leave_application": ("leave_start", "2026-10-01"), "internship_certificate": ("internship_start", "2026-07-01")}
        for key, (field, value) in expectations.items():
            with self.subTest(key=key):
                result = extract_document_fields(text, key)
                self.assertEqual(result["fields"][field], value)
                self.assertIsNone(result["fields"].get("application_date"))

    def test_date_formats_and_ocr_digits(self):
        for raw, normalized in (("01.09.2026", "2026-09-01"), ("2026-09-01", "2026-09-01"),
                ("1/9/2026", "2026-09-01"), ("«1» октября 2026", "2026-10-01"),
                ("01.O9.2O26", "2026-09-01"), ("31.02.2026", None)):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_date(raw), normalized)

    def test_invalid_date_does_not_become_a_value(self):
        result = extract_document_fields("Дата выдачи: 31.02.2026", "study_certificate")
        self.assertIsNone(result["fields"]["issue_date"])
        self.assertEqual(result["field_statuses"]["issue_date"], "unknown")

    def test_labels_spaces_and_wrapped_values(self):
        text = "ФИ0: \nИванов Иван Иванович\nУчебная группа — ИКБО-01-24\nФорма обучения: заочной\nДата начала обучения:\n01.09.2024\nДата окончания обучения: 30.06.2028"
        result = extract_document_fields(text, "study_certificate")
        self.assertEqual(result["fields"]["student_name"], "Иванов Иван Иванович")
        self.assertEqual(result["fields"]["study_form"], "заочная")
        self.assertEqual(result["fields"]["study_start"], "2024-09-01")

    def test_reversed_period_is_reported_without_reordering_or_rejection(self):
        result = extract_document_fields("Отпуск с 02.09.2027 по 01.09.2026", "academic_leave_application")
        validation = validate_document_fields(result)
        self.assertIn("reversed_period", [issue["code"] for issue in validation["issues"]])
        self.assertFalse(validation["blocking"])
        self.assertEqual(result["fields"]["leave_start"], "2027-09-02")

    def test_legacy_labels_and_unknown_type(self):
        for key, schema in DOCUMENT_SCHEMAS.items():
            self.assertEqual(normalize_document_type(schema["label"]), key)
            self.assertEqual(extract_document_fields("", schema["label"])["document_type"], key)
        with self.assertRaises(ValueError):
            extract_document_fields("", "unknown")

    def test_inline_fields_and_multiline_names(self):
        text = "ФИО: Иванов\nИван\nИванович\nГруппа: АБ-12; Курс: 3\nДата заявления: 14.10.2026"
        result = extract_document_fields(text, "academic_leave_application")
        self.assertEqual(result["fields"]["student_name"], "Иванов Иван Иванович")
        self.assertEqual(result["fields"]["group"], "АБ-12")
        self.assertEqual(result["fields"]["course"], 3)
        result = extract_document_fields("ФИО: Иванов Иван Иванович Группа: АБ-12 Курс: 3", "academic_leave_application")
        self.assertEqual(result["fields"]["student_name"], "Иванов Иван Иванович")
        self.assertEqual(result["fields"]["group"], "АБ-12")

    def test_wrapped_period_context_and_unlabelled_student(self):
        text = "Студент Иванов Иван Иванович\nГруппа АБ-12\nПериод обучения\nс 01.09.2024 по 30.06.2028"
        result = extract_document_fields(text, "study_certificate")
        self.assertEqual(result["fields"]["student_name"], "Иванов Иван Иванович")
        self.assertEqual(result["fields"]["study_start"], "2024-09-01")
        self.assertEqual(extract_document_fields(text, "academic_leave_application")["fields"]["group"], "АБ-12")

    def test_period_prefers_immediate_domain_not_background_context(self):
        text = "В период обучения студент проходит практику с 01.07.2026 по 01.08.2026"
        self.assertIsNone(extract_document_fields(text, "study_certificate")["fields"]["study_start"])
        self.assertEqual(extract_document_fields(text, "internship_certificate")["fields"]["internship_start"], "2026-07-01")

    def test_form_placeholders_are_not_person_names(self):
        for text in ("ФИО: Фамилия Имя Отчество", "Справка выдана в том, что студент обучается", "ФИО: не указано"):
            with self.subTest(text=text):
                self.assertIsNone(extract_document_fields(text, "study_certificate")["fields"]["student_name"])


class DocumentLoggingTests(unittest.TestCase):
    def test_complete_report_uses_info_without_personal_values(self):
        result = extract_document_fields(TEXTS["academic_leave_application"], "academic_leave_application", confirmed_visuals())
        messages = []
        with self.assertLogs("decanat.documents", level="INFO") as captured:
            log_document_result(result, messages.append)
        self.assertEqual(captured.records[0].levelno, logging.INFO)
        report = messages[0]
        self.assertIn("8/8", report)
        for name in DOCUMENT_SCHEMAS["academic_leave_application"]["fields"]:
            self.assertIn(name, report)
        self.assertNotIn("Петров", report)
        self.assertNotIn("ИКБО-01-24", report)
        self.assertNotIn("2026-10-14", report)

    def test_warning_includes_every_missing_and_unknown_field(self):
        result = extract_document_fields("", "study_certificate")
        with self.assertLogs("decanat.documents", level="INFO") as captured:
            log_document_result(result)
        self.assertEqual(captured.records[0].levelno, logging.WARNING)
        self.assertIn("0/10", captured.output[0])
        self.assertIn("[UNKNOWN] stamp", captured.output[0])
        self.assertIn("[MISSING] student_name", captured.output[0])

    def test_error_report_hides_exception_message_secrets_and_fields(self):
        with self.assertLogs("decanat.documents", level="INFO") as captured:
            log_document_failure("internship_certificate", RuntimeError("token=SECRET code=LOGIN Иванов"))
        self.assertEqual(captured.records[0].levelno, logging.ERROR)
        self.assertIn("RuntimeError", captured.output[0])
        for value in ("SECRET", "LOGIN", "Иванов"):
            self.assertNotIn(value, captured.output[0])
        self.assertIn("0/10", captured.output[0])


if __name__ == "__main__":
    unittest.main()
