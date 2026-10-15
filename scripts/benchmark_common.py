import csv
import json
import re
import unicodedata
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def validate_document_ids(documents):
    ids = [item.get("document_id") for item in documents]
    if any(not isinstance(value, str) or not re.fullmatch(r"[\w-]+", value) for value in ids) or len(
        ids
    ) != len(set(ids)):
        raise ValueError("Document IDs must be unique and safe filenames")
    return ids


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def write_csv(path, rows, columns=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = columns or (list(rows[0]) if rows else ["status"])
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def norm(value):
    if not isinstance(value, str):
        return value
    text = unicodedata.normalize("NFC", value).casefold().replace("ё", "е")
    for quote in "«»“”„":
        text = text.replace(quote, '"')
    return " ".join(text.split())


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def table(headers, rows):
    return [
        "| " + " | ".join(headers) + " |",
        "|" + "---|" * len(headers),
        *["| " + " | ".join(cell(value) for value in row) + " |" for row in rows],
        "",
    ]
