from utils.document_policy import preliminary_check

FIELD_LABELS = {
    "student_name": "ФИО студента",
    "university": "Учебное заведение",
    "program": "Направление обучения",
    "study_form": "Форма обучения",
    "study_start": "Начало обучения",
    "study_end": "Окончание обучения",
    "document_number": "Номер документа",
    "issue_date": "Дата выдачи",
    "group": "Группа",
    "course": "Курс",
    "leave_reason": "Причина отпуска",
    "leave_start": "Начало отпуска",
    "leave_end": "Окончание отпуска",
    "application_date": "Дата заявления",
    "organization": "Организация",
    "internship_start": "Начало практики",
    "internship_end": "Окончание практики",
    "supervisor_name": "Руководитель практики",
    "grade": "Оценка",
    "signature": "Подпись",
    "student_signature": "Подпись студента",
    "stamp": "Печать",
}


def document_feedback(analysis):
    extraction = analysis.get("extraction")
    if not extraction:
        return (
            "Документ обработан, но основные поля не удалось распознать. Нужна проверка сотрудника деканата."
        )
    lines = [f"Результат проверки: {extraction['document_label']}", ""]
    for name, value in extraction["fields"].items():
        status = extraction["field_statuses"].get(name, "unknown")
        if status == "found" and value is not None:
            value = "обнаружен визуальный кандидат" if isinstance(value, bool) else str(value)
        else:
            value = "не найдено" if status == "missing" else "не удалось однозначно проверить"
        lines.append(f"{FIELD_LABELS.get(name, name)}: {value}")
    check = preliminary_check(extraction)
    conclusion = {
        "document_ok": "Все обязательные сведения найдены.",
        "missing_fields": "Часть обязательных сведений не найдена. Проверь документ.",
        "unknown": "Для окончательной проверки части сведений нужен сотрудник деканата.",
    }
    lines.extend(("", conclusion[check["status"]]))
    if analysis.get("document_type_matches_selection") is False:
        lines.append("Определённый тип документа отличается от выбранного. Обрати на это внимание.")
    lines.append("Это предварительная проверка. Окончательное решение принимает деканат.")
    return "\n".join(lines)
