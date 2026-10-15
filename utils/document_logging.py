"""Status-only document reports; never include OCR text, field values or exception messages."""

import logging

from utils.document_fields import empty_extraction

logger = logging.getLogger("decanat.documents")
logger.setLevel(logging.INFO)
logger.addHandler(logging.NullHandler())


def extraction_counts(result):
    statuses = result["field_statuses"]
    return {
        "expected": len(statuses),
        "found": sum(status == "found" for status in statuses.values()),
        "missing": [name for name, status in statuses.items() if status == "missing"],
        "unknown": [name for name, status in statuses.items() if status in ("unknown", "ambiguous", "error")],
    }


def format_document_report(result, source):
    counts = extraction_counts(result)
    labels = {
        "found": ("OK", "найдено"),
        "missing": ("MISSING", "не найдено"),
        "unknown": ("UNKNOWN", "не удалось проверить или распознать"),
        "ambiguous": ("UNKNOWN", "найдено несколько разных значений"),
        "error": ("ERROR", "обработка поля не выполнена"),
    }
    lines = [
        "========== ОБРАБОТКА ДОКУМЕНТА ==========",
        f"Тип: {result['document_label']} ({result['document_type']})",
        f"Источник: {source}",
        f"Ожидается полей: {counts['expected']}",
    ]
    if "detected_document_type" in result:
        lines += [
            f"Выбранный тип: {result.get('selected_document_type')}",
            f"Определённый тип: {result['detected_document_type']}",
            f"Название: {result.get('document_title') or 'не определено'}",
            f"Совпадение типов: {result.get('document_type_matches_selection')}",
        ]
    for name, status in result["field_statuses"].items():
        marker, description = labels[status]
        lines.append(f"[{marker}] {name} — {description}")
    lines += [
        f"Найдено полей: {counts['found']}/{counts['expected']}",
        "Найденные поля: "
        + (
            ", ".join(name for name, status in result["field_statuses"].items() if status == "found") or "нет"
        ),
        "Отсутствует: " + (", ".join(counts["missing"]) or "нет"),
        "Не удалось проверить: " + (", ".join(counts["unknown"]) or "нет"),
    ]
    # Error dictionaries are constructed in our code using stage names and exception class names.
    for error in result.get("errors", []):
        lines.append(f"[ERROR] {error['stage']}: {error['code']}")
    lines.append("=========================================")
    return "\n".join(lines)


def log_document_result(result, sink=None, source="telegram"):
    counts = extraction_counts(result)
    level = (
        logging.ERROR
        if result.get("errors")
        else logging.WARNING
        if counts["missing"] or counts["unknown"]
        else logging.INFO
    )
    report = format_document_report(result, source)
    logger.log(level, "%s", report)
    # Keep the existing Telegram admin-log queue and its redaction policy.
    if sink is not None:
        sink(f"{logging.getLevelName(level)}\n{report}")


def log_document_failure(document_type, error, sink=None, source="telegram", stage="ocr"):
    result = empty_extraction(document_type, [{"stage": stage, "code": type(error).__name__}])
    result.update(
        selected_document_type=result["document_type"],
        detected_document_type="unknown",
        document_title=None,
        document_type_matches_selection=None,
    )
    result["field_statuses"] = {name: "unknown" for name in result["fields"]}
    log_document_result(result, sink, source)
