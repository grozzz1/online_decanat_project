import re
import unicodedata
from datetime import date

DOCUMENT_SCHEMAS = {
    "study_certificate": {
        "label": "Справка об обучении",
        "fields": (
            "student_name",
            "university",
            "program",
            "study_form",
            "study_start",
            "study_end",
            "document_number",
            "issue_date",
            "signature",
            "stamp",
        ),
        "period": ("study_start", "study_end"),
    },
    "academic_leave_application": {
        "label": "Заявление на академический отпуск",
        "fields": (
            "student_name",
            "group",
            "course",
            "leave_reason",
            "leave_start",
            "leave_end",
            "application_date",
            "student_signature",
        ),
        "period": ("leave_start", "leave_end"),
    },
    "internship_certificate": {
        "label": "Справка о практике",
        "fields": (
            "student_name",
            "organization",
            "internship_start",
            "internship_end",
            "supervisor_name",
            "grade",
            "document_number",
            "issue_date",
            "signature",
            "stamp",
        ),
        "period": ("internship_start", "internship_end"),
    },
}
VISUAL_FIELDS = frozenset(("signature", "student_signature", "stamp"))


def normalize_document_type(value):
    value = " ".join(str(value or "").split()).casefold()
    for key, schema in DOCUMENT_SCHEMAS.items():
        if value in (key, schema["label"].casefold()):
            return key
    return None


def document_label(value):
    key = normalize_document_type(value)
    return DOCUMENT_SCHEMAS[key]["label"] if key else str(value or "")


def normalize_ocr_text(text):
    text = (
        unicodedata.normalize("NFKC", (text or "").replace("№", "\ue000"))
        .replace("\ue000", "№")
        .replace("\r", "\n")
    )
    text = re.sub(r"[\u200b\ufeff]", "", text)
    text = re.sub(r"\bФ[ИI]0\b", "ФИО", text, flags=re.I)
    text = text.replace("Nº", "№").replace("N°", "№")
    return "\n".join(re.sub(r"[^\S\n]+", " ", line).strip() for line in text.splitlines())


MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "января",
            "февраля",
            "марта",
            "апреля",
            "мая",
            "июня",
            "июля",
            "августа",
            "сентября",
            "октября",
            "ноября",
            "декабря",
        ),
        1,
    )
}
DIGIT = r"[0-9ОOоoIlІ]"
DATE_PATTERN = (
    rf"(?:{DIGIT}{{4}}-{DIGIT}{{1,2}}-{DIGIT}{{1,2}}|"
    rf"{DIGIT}{{1,2}}\s*[./-]\s*{DIGIT}{{1,2}}\s*[./-]\s*{DIGIT}{{4}}|"
    rf"[«\"']?{DIGIT}{{1,2}}[»\"']?\s+(?:{'|'.join(MONTHS)})\s+{DIGIT}{{4}})"
)
DATE_RE = re.compile(DATE_PATTERN, re.I)


def normalize_date(value):
    value = re.sub(r"[«»\"']", "", value.strip().lower())

    value = re.sub(
        r"(?<![a-zа-яё])[0-9оoilі]+(?![a-zа-яё])",
        lambda m: m[0].translate(str.maketrans({"о": "0", "o": "0", "i": "1", "l": "1", "і": "1"})),
        value,
    )
    try:
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", value):
            year, month, day = map(int, value.split("-"))
        elif re.fullmatch(r"\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{4}", value):
            day, month, year = map(int, re.split(r"\s*[./-]\s*", value))
        else:
            match = re.fullmatch(r"(\d{1,2})\s+([а-я]+)\s+(\d{4})", value)
            if not match or match[2] not in MONTHS:
                return None
            day, month, year = int(match[1]), MONTHS[match[2]], int(match[3])
        return date(year, month, day).isoformat()
    except ValueError:
        return None


ALIASES = {
    "student_name": r"(?:Ф\s*\.?\s*И\s*\.?\s*О\s*\.?|Фамилия[ ,]+имя[ ,]+отчество|ФИО\s+студента|ФИО\s+обучающегося|Студент(?:ка)?|Обучающийся|Обучающаяся)",
    "university": r"(?:(?:Название|Наименование)\s+(?:вуза|университета|образовательной\s+организации)|Учебное\s+заведение|ВУЗ|Университет)",
    "program": r"(?:Направление(?:\s+подготовки)?|Образовательная\s+программа|Специальность|Программа\s+обучения)",
    "study_form": r"(?:Форма\s+обучения)",
    "group": r"(?:Учебная\s+группа|Группа|Группы)",
    "course": r"(?:Курс|Курса)",
    "leave_reason": r"(?:Причина(?:\s+отпуска)?|Основание(?:\s+для\s+отпуска)?)",
    "organization": r"(?:Место\s+прохождения\s+практики|База\s+практики|(?:Название|Наименование)\s+организации|Организация|Предприятие)",
    "supervisor_name": r"(?:ФИО\s+руководителя(?:\s+практики)?|Руководитель\s+практики|Руководитель)",
    "grade": r"(?:Итоговая\s+оценка|Оценка|Результат\s+практики)",
    "document_number": r"(?:Номер\s+(?:документа|справки)|Регистрационный\s+номер|Рег\.?\s*№|№)",
    "study_start": r"(?:(?:Дата\s+начала|Начало)(?:\s+периода)?\s+обучения|Дата\s+зачисления|Обучается\s+с)",
    "study_end": r"(?:(?:Дата\s+окончания|Окончание)(?:\s+периода)?\s+обучения|Обучается\s+до)",
    "leave_start": r"(?:Дата\s+начала\s+(?:академического\s+)?отпуска|Начало\s+отпуска|Отпуск\s+с)",
    "leave_end": r"(?:Дата\s+окончания\s+(?:академического\s+)?отпуска|Окончание\s+отпуска|Отпуск\s+до)",
    "internship_start": r"(?:Дата\s+начала\s+практики|Начало\s+практики|Практика\s+с)",
    "internship_end": r"(?:Дата\s+окончания\s+практики|Окончание\s+практики|Практика\s+до)",
    "issue_date": r"(?:Дата\s+выдачи(?:\s+справки)?|Дата\s+справки|Выдана|Выдано)",
    "application_date": r"(?:Дата\s+заявления|Дата\s+составления|Дата)",
}
ALL_LABELS = re.compile(r"^(?:" + "|".join(ALIASES.values()) + r")\s*[:—–-]", re.I)
INLINE_LABEL = re.compile(r"(?<!\w)(?:" + "|".join(ALIASES.values()) + r")\s*[:—–-]", re.I)


def _label_values(text, alias, multiline=False):

    pattern = re.compile(rf"(?:^|[;\n])\s*(?:{alias})\s*[:—–-][ \t]*([^\n;]*)", re.I)
    lines = text.splitlines()
    values = []
    for match in pattern.finditer(text):
        value = match[1].strip()
        index = text[: match.end()].count("\n")
        name_field = alias in (ALIASES["student_name"], ALIASES["supervisor_name"])

        for following in lines[index + 1 : index + 4]:
            if (
                not following
                or ALL_LABELS.match(following)
                or re.match(
                    r"^(?:Подпись|Печать|М\.?\s*П\.?|Дата|Справка|Заявление|Период|Срок|Прошу|Прошел|Прошла|Прошёл|Обучается|Отпуск|Практика|с\s+\d)\b",
                    following,
                    re.I,
                )
            ):
                break
            if value and (
                not multiline
                or (name_field and (len(value.split()) >= 3 or not re.fullmatch(NAME, following, re.I)))
            ):
                break
            value = f"{value} {following}".strip()
        if value and not re.fullmatch(r"[_ .—–-]+", value):
            values.append(value)
    return values


NAME = r"[А-ЯЁA-Z][а-яёa-z]+(?:-[А-ЯЁA-Z]?[а-яёa-z]+)?"
FULL_NAME = re.compile(rf"(?:{NAME}\s+){{1,2}}{NAME}", re.I)


def _name(value):
    value = re.sub(r"^(?:студент[ау]?|студентки|обучающемуся|обучающейся)\s+", "", value, flags=re.I)
    value = value.strip(" .,:;_()")

    if value.casefold() in ("фамилия имя отчество", "фио студента", "не указано", "нет данных"):
        return None
    if set(value.casefold().split()) & {
        "в",
        "о",
        "том",
        "что",
        "справка",
        "выдана",
        "выдано",
        "является",
        "обучается",
        "подпись",
        "форма",
        "обучения",
    }:
        return None
    if FULL_NAME.fullmatch(value) and len(value.split()) in (2, 3):
        return " ".join(value.split())
    return None


def _resolve(values):
    unique = {}
    invalid = False
    for value in values:
        if value is None:
            invalid = True
        else:
            unique.setdefault(str(value).casefold(), value)
    if len(unique) > 1:
        return None, "ambiguous"
    if invalid:
        return None, "unknown"
    return (next(iter(unique.values())), "found") if unique else (None, "missing")


def _date_values(text, field):
    values = []
    alias = ALIASES[field]
    for match in re.finditer(rf"(?:{alias})\s*[:—–-]?\s*({DATE_PATTERN})", text, re.I):
        if field == "application_date" and match[0].lower().startswith("дата"):
            prefix = text[: match.start()].rsplit("\n", 1)[-1].strip()
            if prefix or re.match(r"Дата\s+(?:начала|окончания|рождения|выдачи)", match[0], re.I):
                continue
        values.append(normalize_date(match[1]))
    return values


def _period_values(text, document_type):
    domains = {
        "study_certificate": r"(?:обучени[еяю]|обучается|срок\s+обучения)",
        "academic_leave_application": r"(?:академическ\w*\s+отпуск\w*|отпуск\w*)",
        "internship_certificate": r"(?:практик\w*)",
    }
    pairs = []
    pattern = re.compile(
        rf"\bс\s*({DATE_PATTERN})\s*(?:г(?:ода|\.)?)?\s*(?:по|до|[—–-])\s*({DATE_PATTERN})", re.I
    )
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 130) : match.start()]
        segments = re.split(r"[;\n]", prefix)
        prefix = segments[-1] or (segments[-2] if len(segments) > 1 else "")
        context = [
            (found.end(), key)
            for key, domain in domains.items()
            for found in re.finditer(domain, prefix, re.I)
        ]
        if context and max(context)[1] == document_type:
            pairs.append((normalize_date(match[1]), normalize_date(match[2])))
    return pairs


def _visual_value(field, visuals):
    if not visuals:
        return None, "unknown"
    result = visuals.get(field) or {}
    if result.get("detected"):
        if result.get("status") != "detected" or result.get("confidence", 0) < 0.70:
            return None, "unknown"
        if field == "student_signature" and result.get("role") != "student":
            return None, "unknown"
        return True, "found"
    pages = visuals.get("pages") or []
    complete = pages and all(page.get("analysis_status") == "complete" for page in pages)
    if result.get("status") == "not_detected" and complete:
        return False, "missing"
    return None, "unknown"


def empty_extraction(document_type, errors=None):
    custom = isinstance(document_type, str) and document_type.startswith("custom_")
    key = document_type if custom else normalize_document_type(document_type)
    if not key:
        raise ValueError("Неподдерживаемый тип документа.")
    schema = DOCUMENT_SCHEMAS.get(key, {"label": "Пользовательский документ", "fields": {}})
    return {
        "document_type": key,
        "document_label": schema["label"],
        "fields": {name: None for name in schema["fields"]},
        "field_statuses": {
            name: "unknown" if name in VISUAL_FIELDS else "missing" for name in schema["fields"]
        },
        "field_sources": {
            name: "unknown" if name in VISUAL_FIELDS else "missing" for name in schema["fields"]
        },
        "field_evidence": {},
        "errors": list(errors or []),
    }


def extract_document_fields(text, document_type, visuals=None):
    result = empty_extraction(document_type)
    key = result["document_type"]
    text = "\n".join(
        INLINE_LABEL.sub(lambda m: ("\n" if m.start() > 0 else "") + m[0], line)
        for line in normalize_ocr_text(text).splitlines()
    )
    period = DOCUMENT_SCHEMAS[key]["period"]
    pairs = _period_values(text, key)
    for field in result["fields"]:
        if field in VISUAL_FIELDS:
            value, status = _visual_value(field, visuals)
        else:
            values = _label_values(
                text,
                ALIASES[field],
                multiline=field
                in (
                    "student_name",
                    "supervisor_name",
                    "university",
                    "program",
                    "organization",
                    "leave_reason",
                ),
            )
            values = [
                value
                for value in values
                if value.casefold().strip(" .")
                not in (
                    "не указано",
                    "не указан",
                    "не указана",
                    "не найдено",
                    "не заполнено",
                    "отсутствует",
                    "нет данных",
                    "неизвестно",
                    "н/д",
                )
            ]
            if field.endswith(("_date", "_start", "_end")):
                values = _date_values(text, field)
                if field in period:
                    values += [pair[period.index(field)] for pair in pairs]

                if field == "issue_date":
                    values += [
                        normalize_date(m[1])
                        for m in re.finditer(
                            rf"(?:справка\s*)?№\s*[^\s\n]+\s+от\s+({DATE_PATTERN})", text, re.I
                        )
                    ]
            elif field in ("student_name", "supervisor_name"):
                values = [_name(value) for value in values]
                if field == "student_name" and not values:
                    values += [
                        _name(m[1])
                        for m in re.finditer(
                            r"(?:^|\n)\s*(?:от\s+)?(?:студент[а-я]*|обучающ[а-я]*)\s+([^\n]+)", text, re.I
                        )
                    ]
                    for match in re.finditer(
                        r"(?:выдана?\s+(?:студент[ау]?\s+)?|настоящая\s+справка\s+выдана?\s+)([^\n,]+)",
                        text,
                        re.I,
                    ):
                        candidate = re.split(
                            r"\s+(?:в\s+том|о\s+том|является|обучается)\b", match[1], flags=re.I
                        )[0]
                        parsed = _name(candidate)
                        if parsed:
                            values.append(parsed)
            elif field == "university" and not values:
                values = [
                    m[1].strip()
                    for m in re.finditer(
                        r"(?:^|\n)\s*((?:(?:ФГБОУ|ФГАОУ|ГБОУ|НИУ)\s+[^\n]*|[А-ЯЁA-Z][^\n]*университет[^\n]*))(?=\n|$)",
                        text,
                        re.I,
                    )
                ]
            elif field == "group" and not values:
                values = [
                    m[1]
                    for m in re.finditer(
                        r"\bгрупп[аы]\s+([А-ЯЁA-Z0-9][А-ЯЁA-Z0-9/-]*\d[А-ЯЁA-Z0-9/-]*)", text, re.I
                    )
                ]
            elif field == "course":
                values = [
                    int(value) if re.fullmatch(r"\d{1,2}(?:\s*курс[ае]?)?", value, re.I) else None
                    for value in [re.sub(r"\s*курс[ае]?$", "", value, flags=re.I).strip() for value in values]
                ]
                if not values:
                    values = [
                        int(m[1])
                        for m in re.finditer(r"\b(\d{1,2})\s*[-–]?(?:го\s+)?курс[ае]?\b", text, re.I)
                    ]
            elif field == "study_form":
                forms = {
                    "очная": "очная",
                    "очной": "очная",
                    "заочная": "заочная",
                    "заочной": "заочная",
                    "очно-заочная": "очно-заочная",
                    "очно-заочной": "очно-заочная",
                }
                if not values:
                    values = [
                        m[1]
                        for m in re.finditer(
                            r"\b(очно[-– ]заочн[а-я]+|заочн[а-я]+|очн[а-я]+)\s+форм[а-я]*\s+обучения",
                            text,
                            re.I,
                        )
                    ]
                values = [
                    forms.get(
                        re.sub(r"\s+форм[а-я]*.*$", "", value.lower())
                        .replace("–", "-")
                        .replace("очно заоч", "очно-заоч")
                    )
                    for value in values
                ]
            elif field == "document_number":
                values += [m[1] for m in re.finditer(r"№\s*([\w/-]+)", text)]
                values = [
                    re.split(r"\s+от\s+", re.sub(r"^№\s*", "", value).strip(), flags=re.I)[0]
                    for value in values
                ]
                values = [value if re.fullmatch(r"[\w./-]+", value) else None for value in values]
            elif field == "leave_reason" and not values:
                values = [
                    m[1].strip(" .")
                    for m in re.finditer(r"(?:в\s+связи\s+с|по\s+причине)\s+([^\n.;]+)", text, re.I)
                ]
                values = [
                    re.split(rf"\s+с\s+(?={DATE_PATTERN})", value, flags=re.I)[0].strip() for value in values
                ]
            value, status = _resolve(values)
        result["fields"][field] = value
        result["field_statuses"][field] = status
        result["field_sources"][field] = (
            ("cv" if field in VISUAL_FIELDS else "regex")
            if status == "found" or (field in VISUAL_FIELDS and value is False)
            else "missing"
            if status == "missing"
            else "unknown"
        )
    return result
