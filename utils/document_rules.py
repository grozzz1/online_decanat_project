"""Compatibility API; pure policy lives in document_policy, QA in question_answering."""

from utils.document_fields import ALIASES as ALIASES
from utils.document_fields import DOCUMENT_SCHEMAS as DOCUMENT_SCHEMAS
from utils.document_fields import VISUAL_FIELDS as VISUAL_FIELDS
from utils.document_policy import PUBLIC_FIELDS as PUBLIC_FIELDS
from utils.document_policy import SECTIONS as SECTIONS
from utils.document_policy import answer_question_python as answer_question_python
from utils.document_policy import export_fields as export_fields
from utils.document_policy import export_validation as export_validation
from utils.document_policy import preliminary_check as preliminary_check


def answer_question(question: dict, extraction: dict) -> dict:
    """Return the same shared, verified result used by both interfaces."""
    from utils.question_answering import get_question_engine

    return get_question_engine().answer(question, extraction)["result"]
