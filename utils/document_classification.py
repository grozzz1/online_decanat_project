"""Content classification, independent of the type selected in the UI."""
import re

from utils.document_fields import normalize_ocr_text


TITLES = {
    "study_certificate": r"справка\s+об\s+обучени[ия]",
    "academic_leave_application": r"заявление(?:\s+на|\s+о\s+предоставлении)?\s+академическ\w*\s+отпуск\w*",
    "internship_certificate": r"справка\s+о\s+(?:прохождении\s+)?практик[еаи]",
}
CUES = {
    "study_certificate": (r"форма\s+обучения", r"направление\s+подготовки", r"период[а]?\s+обучения", r"сведения\s+об\s+обучении"),
    "academic_leave_application": (r"академическ\w*\s+отпуск", r"причина\s+отпуска", r"дата\s+заявления", r"(?:учебная\s+)?группа"),
    "internship_certificate": (r"(?:руководител[ья]|дата\s+начала)\s+практики", r"название\s+организации", r"прохождении\s+практики", r"оценка\s*:"),
}


def _ocr_lines(pages):
    lines = []
    for page in pages or []:
        groups = {}
        for word in page.get("ocr_words", []):
            groups.setdefault(tuple(word.get("line", ())), []).append(word)
        for words in groups.values():
            lines.append(" ".join(word["text"] for word in sorted(words, key=lambda word: word["bbox"][0])))
    return lines


def classify_document(text, pages=None):
    lines = normalize_ocr_text(text).splitlines() + _ocr_lines(pages)
    headings = {}
    for key, pattern in TITLES.items():
        for line in lines:
            match = re.search(pattern, line, re.I)
            if match:
                headings[key] = match[0].strip()
                break
    # A generic 'ЗАЯВЛЕНИЕ' heading needs content confirming academic leave.
    combined = "\n".join(lines)
    if "academic_leave_application" not in headings and re.search(r"академическ\w*\s+отпуск", combined, re.I):
        heading = next((line.strip() for line in lines if re.fullmatch(r"\s*ЗАЯВЛЕНИЕ\s*", line, re.I)), None)
        if heading:
            headings["academic_leave_application"] = heading
    if len(headings) == 1:
        key = next(iter(headings))
        return {"document_title": headings[key], "detected_document_type": key, "classification_basis": "heading"}
    if len(headings) > 1:
        return {"document_title": None, "detected_document_type": "unknown", "classification_basis": "conflicting_headings"}
    scores = {key: sum(bool(re.search(cue, combined, re.I)) for cue in cues) for key, cues in CUES.items()}
    ordered = sorted(scores, key=scores.get, reverse=True)
    key = ordered[0]
    if scores[key] >= 2 and scores[key] >= scores[ordered[1]] + 1:
        return {"document_title": None, "detected_document_type": key, "classification_basis": "content"}
    return {"document_title": None, "detected_document_type": "unknown", "classification_basis": "insufficient_evidence"}
