"""Real model checks on new synthetic prose; not part of the 30-document accuracy score."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.benchmark_common import save
from tests.fixtures import TEXTS, confirmed_visuals
from utils.document_fields import extract_document_fields
from utils.hybrid_extraction import supplement_fields
from utils.question_answering import get_question_engine


def validate_results(results):
    expected = {
        "university_in_prose",
        "absent_university",
        "separate_dates",
        "missing_start_not_issue_date",
        "complex-0",
        "complex-1",
    }
    by_name = {item["case"]: item for item in results}
    failed = sorted(expected - by_name.keys())
    for name, item in by_name.items():
        diagnostics = item["diagnostics"]
        valid = bool(diagnostics.get("llm_called"))
        if name.startswith("complex-"):
            source = "rule:2.1" if name == "complex-0" else "rule:1.3"
            valid = valid and item["result"]["answerable"] and source in item["result"]["sources"]
            valid = valid and diagnostics.get("verified", False) and not diagnostics.get("technical_error")
        else:
            valid = valid and item.get("passed", False) and diagnostics.get("status") == "completed"
        if not valid:
            failed.append(name)
    return {"ok": not failed and len(results) == len(expected), "scenarios": len(results), "failed": failed}


def run():
    # These texts are synthetic probes, never reference_results.json content.
    cases = [
        (
            "university_in_prose",
            "study_certificate",
            "\n".join(line for line in TEXTS["study_certificate"].splitlines() if not line.startswith("ВУЗ:"))
            + "\nМесто учёбы — Учебная академия «Пример».",
            {"university": "Учебная академия «Пример»"},
        ),
        (
            "absent_university",
            "study_certificate",
            "\n".join(
                line for line in TEXTS["study_certificate"].splitlines() if not line.startswith("ВУЗ:")
            ),
            {"university": None},
        ),
        (
            "separate_dates",
            "study_certificate",
            TEXTS["study_certificate"].replace(
                "Период обучения с 01.09.2024 по 30.06.2028",
                "Обучение началось 01.09.2024.\nОбучение завершилось 30.06.2028.",
            ),
            {"study_start": "2024-09-01", "study_end": "2028-06-30"},
        ),
        (
            "missing_start_not_issue_date",
            "study_certificate",
            TEXTS["study_certificate"].replace(
                "Период обучения с 01.09.2024 по 30.06.2028", "Окончание обучения: 30.06.2028"
            ),
            {"study_start": None},
        ),
    ]
    results = []
    for name, kind, text, expected in cases:
        baseline = extract_document_fields(text, kind, confirmed_visuals())
        final = supplement_fields(baseline, text)
        result = {
            "case": name,
            "expected": expected,
            "before": {k: baseline["fields"][k] for k in expected},
            "after": {k: final["fields"][k] for k in expected},
            "diagnostics": final["hybrid"],
        }
        result["passed"] = result["after"] == expected
        results.append(result)
        print(name, result["passed"], final["hybrid"]["status"], flush=True)
    engine = get_question_engine()
    for index, text in enumerate(
        [
            "Какие источники ответа разрешены сервису?",
            "Может ли сервис сам принять документ и какие у него границы?",
        ]
    ):
        answered = engine.answer(
            {"question_id": f"complex-{index}", "question": text},
            extract_document_fields(TEXTS["study_certificate"], "study_certificate", confirmed_visuals()),
        )
        results.append({"case": f"complex-{index}", **answered})
        print(f"complex-{index}", answered["result"]["answerable"], answered["diagnostics"], flush=True)
    save(ROOT / "benchmark_results" / "local_ai" / "stress_results.json", results)
    validation = validate_results(results)
    save(ROOT / "benchmark_results" / "local_ai" / "probe_validation.json", validation)
    if not validation["ok"]:
        raise SystemExit("Real model probes failed: " + ", ".join(validation["failed"]))


if __name__ == "__main__":
    run()
