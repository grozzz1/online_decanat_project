import argparse
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from importlib.metadata import version
from io import BytesIO
from pathlib import Path
from time import perf_counter
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.benchmark_common import load, norm, save, validate_document_ids, write_csv
from utils.document_fields import empty_extraction
from utils.document_logging import extraction_counts
from utils.document_rules import PUBLIC_FIELDS, answer_question, export_fields, export_validation
from utils.ocr import analyze_document


def run(args):
    documents = load(args.dataset / "documents.json")
    questions = defaultdict(list)
    for question in load(args.dataset / "questions.json"):
        questions[question["document_id"]].append(question)
    ids = validate_document_ids(documents)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "processing.log").write_text("", encoding="utf-8")
    predictions = []
    archive_path = args.archive or args.dataset / "documents.zip"
    with ZipFile(archive_path) as archive:
        for index, document in enumerate(documents, 1):
            started = perf_counter()
            try:
                data = archive.read(document[args.representation])
                result = analyze_document(BytesIO(data), document["document_type"])
                result.pop("previews", None)
            except Exception as error:
                extraction = empty_extraction(
                    document["document_type"], [{"stage": "processing", "code": type(error).__name__}]
                )
                extraction["field_statuses"] = dict.fromkeys(extraction["fields"], "unknown")
                result = {
                    "extraction": extraction,
                    "detected_document_type": "unknown",
                    "document_title": None,
                    "ocr_text": "",
                    "processing_error": type(error).__name__,
                }
            result["elapsed_seconds"] = perf_counter() - started
            result["representation"] = args.representation
            result["document_id"] = document["document_id"]
            save(args.output / "raw" / f"{document['document_id']}.json", result)
            (args.output / "raw" / f"{document['document_id']}.txt").write_text(
                result.get("ocr_text", result.get("recognized", "")), encoding="utf-8"
            )
            extraction = result["extraction"]
            predictions.append(
                {
                    "document_id": document["document_id"],
                    "document_type": extraction["document_type"],
                    "fields": export_fields(extraction),
                    "validation": export_validation(extraction),
                    "answers": [
                        answer_question(question, extraction)
                        for question in questions[document["document_id"]]
                    ],
                }
            )
            counts = extraction_counts(extraction)
            lines = [f"[{index}/{len(documents)}] {document['document_id']} | {document['document_type']}"]
            lines += [f"  {name}: {status.upper()}" for name, status in extraction["field_statuses"].items()]
            lines += [
                f"  Found {counts['found']}/{counts['expected']}; {result['elapsed_seconds']:.3f}s; detected={result['detected_document_type']}"
            ]
            message = "\n".join(lines)
            print(message, flush=True)
            with (args.output / "processing.log").open("a", encoding="utf-8") as output:
                output.write(message + "\n")
    save(args.output / "results.json", {"version": "1.0", "documents": predictions})
    save(
        args.output / "run_metadata.json",
        {
            "documents": len(ids),
            "representation": args.representation,
            "references_read_during_extraction": False,
            "development_set": True,
            "python": sys.version,
            "archive": str(archive_path.resolve()),
            "packages": {
                name: version(name)
                for name in ("pytesseract", "Pillow", "opencv-python-headless", "numpy", "pypdfium2")
            },
            "pipeline_sha256": {
                name: hashlib.sha256(
                    (Path(__file__).resolve().parents[1] / "utils" / name).read_bytes()
                ).hexdigest()
                for name in (
                    "ocr.py",
                    "document_fields.py",
                    "document_classification.py",
                    "document_cv.py",
                    "document_policy.py",
                    "document_validation.py",
                )
            },
        },
    )


def csv_file(path, rows):
    if rows:
        write_csv(path, rows)
    else:
        path.write_text("", encoding="utf-8-sig")


def report(args):

    references = {item["document_id"]: item for item in load(args.reference)["documents"]}
    predictions = {item["document_id"]: item for item in load(args.output / "results.json")["documents"]}
    if set(references) != set(predictions):
        raise ValueError("Prediction IDs and reference IDs differ")
    groups, fields, rows, errors = defaultdict(list), defaultdict(list), [], []
    for document_id, reference in references.items():
        prediction = predictions[document_id]
        raw = load(args.output / "raw" / f"{document_id}.json")
        unknowns = {
            PUBLIC_FIELDS.get(name, name)
            for name, status in raw["extraction"]["field_statuses"].items()
            if status in ("unknown", "ambiguous", "error")
        }
        correct = missed = wrong = 0
        for field, expected in reference["fields"].items():
            actual = prediction["fields"].get(field)
            good = type(actual) is type(expected) and norm(actual) == norm(expected)
            absent = actual in (None, "не найдено") or actual is False
            expected_present = expected not in (None, "не найдено") and expected is not False
            category = (
                "missed" if not good and expected_present and absent else "wrong" if not good else "correct"
            )
            correct += good
            missed += category == "missed"
            wrong += category == "wrong"
            fields[(reference["document_type"], field)].append((int(good), category, field in unknowns))
            if not good:
                errors.append(
                    {
                        "document_id": document_id,
                        "document_type": reference["document_type"],
                        "field": field,
                        "expected": expected,
                        "actual": actual,
                        "category": category,
                        "unknown": field in unknowns,
                    }
                )
        total = len(reference["fields"])
        classified = raw.get("detected_document_type") == reference["document_type"]
        row = {
            "document_id": document_id,
            "document_type": reference["document_type"],
            "correct": correct,
            "fields": total,
            "field_accuracy": correct / total,
            "missed": missed,
            "wrong": wrong,
            "unknown": len(unknowns),
            "detected_document_type": raw.get("detected_document_type", "unknown"),
            "classification_correct": classified,
            "seconds": raw["elapsed_seconds"],
        }
        rows.append(row)
        groups["all"].append(row)
        groups[reference["document_type"]].append(row)
    summary = []
    for group, values in groups.items():
        total = sum(row["fields"] for row in values)
        summary.append(
            {
                "group": group,
                "documents": len(values),
                "fields": total,
                "correct": sum(row["correct"] for row in values),
                "field_accuracy_micro": sum(row["correct"] for row in values) / total,
                "field_accuracy_macro": sum(row["field_accuracy"] for row in values) / len(values),
                "missed": sum(row["missed"] for row in values),
                "wrong": sum(row["wrong"] for row in values),
                "unknown": sum(row["unknown"] for row in values),
                "classification_accuracy": sum(row["classification_correct"] for row in values) / len(values),
                "classification_errors": sum(not row["classification_correct"] for row in values),
                "mean_seconds": sum(row["seconds"] for row in values) / len(values),
            }
        )
    field_rows = [
        {
            "document_type": kind,
            "field": field,
            "correct": sum(item[0] for item in values),
            "total": len(values),
            "accuracy": sum(item[0] for item in values) / len(values),
            "missed": sum(item[1] == "missed" for item in values),
            "wrong": sum(item[1] == "wrong" for item in values),
            "unknown": sum(item[2] for item in values),
        }
        for (kind, field), values in fields.items()
    ]
    csv_file(args.output / "summary.csv", summary)
    csv_file(args.output / "field_accuracy.csv", field_rows)
    csv_file(args.output / "document_results.csv", rows)
    if not errors:
        with (args.output / "errors.csv").open("w", encoding="utf-8-sig") as output:
            output.write("document_id,document_type,field,expected,actual,category,unknown\n")
    else:
        csv_file(args.output / "errors.csv", errors)
    official = None
    if args.evaluate:
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(args.evaluate),
                "--reference",
                str(args.reference),
                "--predictions",
                str(args.output / "results.json"),
                "--report",
                str(args.output / "official_evaluation.json"),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        (args.output / "evaluate_stdout.json").write_text(completed.stdout, encoding="utf-8")
        official = json.loads(completed.stdout) if completed.stdout else {"error": completed.stderr}
    lines = [
        "# Измерения на предоставленной открытой выборке",
        "",
        "30 исходных документов: PNG и PDF — альтернативные представления, а не 60 независимых наблюдений.",
        "Эта выборка использована при разработке. Итоговые метрики не являются независимой оценкой качества.",
        "Извлечение не читает reference_results.json; сравнение выполняется отдельной командой после сохранения результатов.",
        "",
        "| Группа | Документов | Правильно/полей | Micro | Macro | Пропуски | Ошибочные значения | Unknown | Тип | Среднее, с |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(
            f"| {row['group']} | {row['documents']} | {row['correct']}/{row['fields']} | {row['field_accuracy_micro']:.2%} | {row['field_accuracy_macro']:.2%} | {row['missed']} | {row['wrong']} | {row['unknown']} | {row['classification_accuracy']:.2%} | {row['mean_seconds']:.3f} |"
        )
    lines += [
        "",
        "Macro: сначала среднее по полям каждого документа, затем по документам (официальная метрика). Micro: по всем полям вместе.",
        "Пропуск: эталон содержит значение/отметку, предсказание — отсутствие. Ошибочное значение: остальные несовпадения.",
        "Проверенные отсутствующие поля тоже учитываются в accuracy. 9/10 присутствующих полей одной справки не равны accuracy всей выборки.",
        "Unknown в исходном JSON сохраняется как null. Официальная схема не допускает null для отметок: адаптер экспортирует false; это явно учтено отдельным столбцом unknown.",
        "",
        "## По каждому полю",
        "",
        "| Тип | Поле | Правильно | Точность |",
        "|---|---|---:|---:|",
    ]
    for row in field_rows:
        lines.append(
            f"| {row['document_type']} | {row['field']} | {row['correct']}/{row['total']} | {row['accuracy']:.2%} |"
        )
    if official:
        lines += [
            "",
            "## Официальный evaluate.py",
            "",
            "```json",
            json.dumps({k: v for k, v in official.items() if k != "errors"}, ensure_ascii=False, indent=2),
            "```",
        ]
    lines += [
        "",
        "## Ошибки и ограничения",
        "",
        "Конкретные несовпадения — errors.csv, исходный OCR и полные результаты — raw/.",
        "OCR может путать буквы/номера и терять заголовки; классификация также использует координатный вывод image_to_data.",
        "CV использует эвристики: возможны пропуски и сомнительные кандидаты. Подлинность отметок не проверяется.",
        "Показатели относятся к читаемым синтетическим документам этого набора; реальные фотографии и другой дизайн требуют отдельной проверки.",
    ]
    (args.output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Two phases: `run` never reads reference answers; `report` only compares saved predictions."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    process = commands.add_parser("run")
    process.add_argument("--dataset", type=Path, required=True)
    process.add_argument("--archive", type=Path)
    process.add_argument("--output", type=Path, default=Path("benchmark_results"))
    process.add_argument("--representation", choices=("image", "pdf"), default="image")
    compare = commands.add_parser("report")
    compare.add_argument("--reference", type=Path, required=True)
    compare.add_argument("--output", type=Path, default=Path("benchmark_results"))
    compare.add_argument("--evaluate", type=Path)
    args = parser.parse_args()
    (run if args.command == "run" else report)(args)


if __name__ == "__main__":
    main()
