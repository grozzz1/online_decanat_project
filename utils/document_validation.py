from utils.document_fields import DOCUMENT_SCHEMAS
from utils.document_policy import preliminary_check


def validate_document_visuals(application_type: str | None, visuals: dict) -> dict:

    return {
        "application_type": application_type,
        "status": "informational",
        "blocking": False,
        "required_elements": [],
        "stamp": visuals["stamp"],
        "student_signature": visuals["student_signature"],
        "manual_review": bool(visuals["warnings"])
        or any(
            item["status"] == "uncertain" or item.get("role") == "unknown" for item in visuals["detections"]
        ),
    }


def validate_document_fields(extraction: dict) -> dict:

    schema = DOCUMENT_SCHEMAS[extraction["document_type"]]
    start, end = schema["period"]
    fields = extraction["fields"]
    issues = [
        {"field": name, "code": status}
        for name, status in extraction["field_statuses"].items()
        if status != "found"
    ]
    if fields[start] and fields[end] and fields[start] > fields[end]:
        issues.append({"fields": [start, end], "code": "reversed_period"})
    check = preliminary_check(extraction)
    return {
        "document_type": extraction["document_type"],
        "status": "informational",
        "blocking": False,
        "required_fields": check["required_fields"],
        "sources": check["sources"],
        "issues": issues,
    }
