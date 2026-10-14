"""Preliminary completeness under the supplied fictional rules.md, sections 3–6."""
import re

from utils.document_fields import ALIASES, DOCUMENT_SCHEMAS, VISUAL_FIELDS


SECTIONS = {"study_certificate": "3", "academic_leave_application": "4", "internship_certificate": "5"}
PUBLIC_FIELDS = {"signature": "signature_present", "student_signature": "signature_present", "stamp": "seal_present"}


def preliminary_check(extraction):
    section = SECTIONS[extraction["document_type"]]
    statuses = extraction["field_statuses"]
    missing = [name for name, status in statuses.items() if status == "missing"]
    unknown = [name for name, status in statuses.items() if status not in ("found", "missing")]
    incomplete = missing + unknown
    def rule(name):
        return f"rule:{section}." + ("3" if name == "stamp" else "2" if name in VISUAL_FIELDS else "1")
    sources = sorted({rule(name) for name in (incomplete or statuses)})
    return {"status": "unknown" if unknown else "missing_fields" if missing else "document_ok",
            "missing_fields": missing, "unknown_fields": unknown, "sources": sources,
            "required_fields": list(statuses), "preliminary_only": True}


def export_fields(extraction):
    """Official schema adapter. Internal unknowns remain null; export must use bool/string."""
    result = {}
    for name, value in extraction["fields"].items():
        public = PUBLIC_FIELDS.get(name, name)
        # The official schema cannot express uncertain visual findings. Export conservatively
        # as false, and expose these unknowns separately in the benchmark report/raw results.
        result[public] = bool(value) if name in VISUAL_FIELDS else "не найдено" if value is None else str(value)
    return result


def export_validation(extraction):
    check = preliminary_check(extraction)
    return {"status": "document_ok" if check["status"] == "document_ok" else "missing_fields",
            "missing_fields": [PUBLIC_FIELDS.get(name, name) for name in check["missing_fields"] + check["unknown_fields"]],
            "sources": check["sources"]}


def answer_question_python(question, extraction):
    """Deterministic answers using extracted fields and the supplied rules, never references."""
    text = question["question"].casefold().replace("ё", "е")
    fields = export_fields(extraction)
    validation = export_validation(extraction)
    value, sources = None, []
    answer = "В документе и в Положении нет информации для ответа на этот вопрос. Обратитесь в деканат."
    if re.search(r"(?:все\s+обязательные|комплектност|документ\s+в\s+порядке)", text):
        value, sources = validation["status"], validation["sources"]
        points = ", ".join(source.removeprefix("rule:") for source in sources)
        answer = (f"Документ содержит все обязательные сведения по пунктам {points} Положения. "
                  "Окончательное решение примет сотрудник деканата.") if value == "document_ok" else (
                  f"Не найдены или не подтверждены поля: {', '.join(validation['missing_fields'])}. Пункты {points} Положения. "
                  "Окончательное решение примет сотрудник деканата.")
    elif re.search(r"кто.*(?:окончательн|решени)", text):
        value, sources = "сотрудник деканата", ["rule:1.3"]
        answer = "Окончательное решение принимает сотрудник деканата, пункт 1.3 Положения."
    else:
        explicit = re.search(r"значение\s+поля\s*[«\"“]", text)
        if not explicit and not re.match(r"\s*(?:какова|какое|какая|укажи|назови)\s+(?:дата|номер|фио|направление|форма|организация|оценка|причина)\b", text):
            return {"question_id": question["question_id"], "answerable": False, "value": None, "answer": answer, "sources": []}
        matches = [(match.end() - match.start(), name) for name in DOCUMENT_SCHEMAS[extraction["document_type"]]["fields"]
                   if name not in VISUAL_FIELDS for match in re.finditer(ALIASES[name], text, re.I)]
        if matches:
            _, name = max(matches)
            public = PUBLIC_FIELDS.get(name, name)
            if fields[public] != "не найдено":
                value, sources = fields[public], [f"field:{public}"]
                answer = f"{value}. Источник: поле {public}."
    return {"question_id": question["question_id"], "answerable": value is not None, "value": value,
            "answer": answer, "sources": sources}


def answer_question(question, extraction):
    """Compatibility API shared by the benchmark, bot and website."""
    from utils.question_answering import get_question_engine
    return get_question_engine().answer(question, extraction)["result"]
