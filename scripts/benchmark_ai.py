"""Real paired experiments. References are read ONLY by evaluate(), after inference."""
import argparse
from collections import Counter
import csv
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.benchmark import norm
from utils.document_fields import VISUAL_FIELDS
from utils.document_rules import export_fields, export_validation
from utils.knowledge import get_knowledge
from utils.local_ai import AIError, get_local_ai
from utils.ocr import analyze_document
from utils.question_answering import (ANSWER_SCHEMA, BASELINE_PROMPT, STRICT_PROMPT,
    get_question_engine, refusal, retrieve_for_question, route_python, validate_generated_answer, validation_intent)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def write_csv(path, rows, columns=None):
    columns = columns or list(rows[0]) if rows else columns or ["status"]
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader(); writer.writerows(rows)


def pipeline_hash(files):
    return hashlib.sha256(b"".join((ROOT / "utils" / file).read_bytes() for file in files)).hexdigest()


def extraction_experiments(args):
    documents = load(args.dataset / "documents.json")
    fingerprint = pipeline_hash(["ocr.py", "document_fields.py", "document_cv.py", "hybrid_extraction.py", "local_ai.py"])
    with ZipFile(args.dataset / "documents.zip") as archive:
        for document in documents:
            for approach in ("regex", "hybrid"):
                path = args.output / "local_ai" / approach / "raw" / (document["document_id"] + ".json")
                if path.exists() and not args.force and load(path).get("pipeline_hash") == fingerprint:
                    continue
                started = perf_counter()
                result = analyze_document(BytesIO(archive.read(document["image"])), document["document_type"], use_ai=approach == "hybrid")
                result.pop("previews", None)
                result.update(document_id=document["document_id"], approach=approach,
                              seconds=perf_counter() - started, pipeline_hash=fingerprint)
                save(path, result)
                hybrid = result["extraction"].get("hybrid", {})
                print(f"{approach} {document['document_id']} {result['seconds']:.3f}s AI={hybrid.get('status', 'not_called')} recovered={len(hybrid.get('accepted', []))}", flush=True)
    save(args.output / "local_ai" / "extraction_environment.json", {
        "pipeline_hash": fingerprint, "python": sys.version, "model": load(ROOT / "models" / "installation.json") if (ROOT / "models" / "installation.json").exists() else None,
        "client_config": vars(get_local_ai().config), "available": get_local_ai().health()["available"],
        "active_runtime": load(ROOT / ".local_ai" / "runtime.json") if (ROOT / ".local_ai" / "runtime.json").exists() else None,
        "dataset": str(args.dataset), "references_read_during_inference": False,
        "input": "PNG", "timing": "Two actual OCR/CV/extraction runs per document, Regex then hybrid; no cached OCR timing estimates."})


def qa_experiments(args):
    documents = load(args.dataset / "documents.json")
    all_questions = load(args.dataset / "questions.json")
    client, knowledge, engine = get_local_ai(), get_knowledge(), get_question_engine()
    save(args.output / "local_ai" / "qa_environment.json", {"runtime": load(ROOT / ".local_ai" / "runtime.json") if (ROOT / ".local_ai" / "runtime.json").exists() else None,
         "model": load(ROOT / "models" / "installation.json"), "config": vars(client.config),
         "prompts": {"baseline": BASELINE_PROMPT, "strict": STRICT_PROMPT},
         "references_read_during_inference": False, "batch_size": 4, "generation_limit": 1024,
         "baseline_note": "No prior Qwen prompt existed; baseline_v1 is newly defined and executed, not a historical result."})
    variants = args.variants.split(",")
    for document in documents:
        questions = [q for q in all_questions if q["document_id"] == document["document_id"]]
        source = args.output / "local_ai" / "hybrid" / "raw" / (document["document_id"] + ".json")
        analysis = load(source)
        extraction = analysis["extraction"]
        for variant in variants:
            path = args.output / "local_ai" / variant / (document["document_id"] + ".json")
            fingerprint = pipeline_hash(["question_answering.py", "knowledge.py", "local_ai.py"]) + hashlib.sha256(source.read_bytes()).hexdigest()
            if path.exists() and not args.force and load(path).get("pipeline_hash") == fingerprint:
                continue
            started, records_before = perf_counter(), len(client.records)
            diagnostics, predictions, raw = [], [], None
            if variant == "python_rag_qwen":
                for question in questions:
                    answered = engine.answer(question, extraction)
                    predictions.append(answered["result"]); diagnostics.append(answered["diagnostics"])
            else:
                snippets = {}
                for question in questions:
                    snippets.update({s["source"]: s for s in retrieve_for_question(question, extraction, knowledge)})
                payload = {"QUESTIONS": questions,
                    "FIELDS": {PUBLIC.get(name, name): value for name, value in extraction["fields"].items()},
                    "VALIDATION": export_validation(extraction),
                    "RULES": [{"source": s["source"], "text": s["text"]} for s in snippets.values()]}
                schema = {"type": "object", "additionalProperties": False, "required": ["answers"],
                          "properties": {"answers": {"type": "array", "minItems": len(questions), "maxItems": len(questions), "items": ANSWER_SCHEMA}}}
                try:
                    raw = client.generate(BASELINE_PROMPT if variant == "qwen_baseline" else STRICT_PROMPT,
                                          payload, schema, purpose=variant, max_tokens=1024)
                    mapping = {answer["question_id"]: answer for answer in raw["answers"]}
                    if len(mapping) != len(questions) or set(mapping) != {q["question_id"] for q in questions}:
                        raise AIError("question_id_mismatch")
                    for question in questions:
                        answer = mapping[question["question_id"]]
                        error = None
                        try:
                            _, expected = route_python(question, extraction, knowledge)
                            validate_generated_answer(answer, question, extraction, list(snippets.values()), expected)
                        except AIError as failure:
                            error = str(failure)
                        predictions.append(answer)  # RAW prompt baseline; never delivered to a user.
                        diagnostics.append({"route": variant, "llm_called": True, "rag_called": True,
                                            "rejections": [error] if error else [], "technical_error": False})
                except AIError as error:
                    predictions = [refusal(q["question_id"], technical=True) for q in questions]
                    diagnostics = [{"route": variant, "llm_called": True, "rag_called": True,
                                    "technical_error": True, "reason": str(error), "rejections": [str(error)]} for _ in questions]
            result = {"document_id": document["document_id"], "variant": variant, "pipeline_hash": fingerprint,
                      "seconds": perf_counter() - started, "answers": predictions, "diagnostics": diagnostics,
                      "inference": list(client.records)[records_before:], "raw": raw}
            save(path, result)
            print(f"{variant} {document['document_id']} {result['seconds']:.2f}s technical_errors={sum(d.get('technical_error', False) for d in diagnostics)}", flush=True)


PUBLIC = {"signature": "signature_present", "student_signature": "signature_present", "stamp": "seal_present"}


def evaluate(args):
    # Evaluation-only boundary. None of the functions above read references.
    references = {d["document_id"]: d for d in load(args.dataset / "reference_results.json")["documents"]}
    questions = load(args.dataset / "questions.json")
    questions_by_id = {q["question_id"]: q for q in questions}
    field_rows, by_name, by_type, qa_rows, errors, performance, rag_rows = [], [], [], [], [], [], []
    for approach in ("regex", "hybrid"):
        counts, timings, name_counts, type_counts = Counter(), [], {}, {}
        exports = []
        for document_id, reference in references.items():
            path = args.output / "local_ai" / approach / "raw" / (document_id + ".json")
            if not path.exists(): continue
            result = load(path); extraction = result["extraction"]; fields = export_fields(extraction)
            timings.append(result["seconds"])
            hybrid = extraction.get("hybrid", {})
            counts["documents"] += 1
            counts["llm_documents"] += hybrid.get("llm_called", False)
            counts["requested_fields"] += len(hybrid.get("requested_fields", []))
            counts["recovered_fields"] += len(hybrid.get("accepted", []))
            counts["rejected_values"] += len(hybrid.get("rejected", {}))
            counts["technical_failures"] += bool(hybrid and hybrid.get("status") not in ("completed", "not_needed"))
            for name, expected in reference["fields"].items():
                actual = fields.get(name); correct = norm(actual) == norm(expected)
                counts["fields"] += 1; counts["correct"] += correct
                counts["missing_values"] += actual == "не найдено"
                counts["missed_present_fields"] += expected != "не найдено" and actual == "не найдено"
                for mapping, key in [(name_counts, (reference["document_type"], name)), (type_counts, reference["document_type"])]:
                    entry = mapping.setdefault(key, Counter()); entry["fields"] += 1; entry["correct"] += correct
                if not correct:
                    errors.append({"experiment": approach, "document_id": document_id, "question_id": "", "reason": "wrong_field", "expected": str(expected), "actual": str(actual)})
            for name in hybrid.get("accepted", []):
                counts["recovered_correctly"] += norm(fields[PUBLIC.get(name, name)]) == norm(reference["fields"][PUBLIC.get(name, name)])
            for name, proposal in hybrid.get("proposals", {}).items():
                if proposal is not None:
                    counts["wrong_proposals"] += norm(str(proposal["value"])) != norm(reference["fields"][PUBLIC.get(name, name)])
            if hybrid.get("llm_called"):
                performance.append({"variant": approach, "document_id": document_id, "purpose": "extraction_fallback",
                                    "status": hybrid["status"], "seconds": hybrid["seconds"], "measurement": "generation_and_verification"})
            exports.append({"document_id": document_id, "document_type": extraction["document_type"], "fields": fields,
                            "validation": export_validation(extraction), "answers": []})
        if not timings: continue
        field_rows.append({"approach": approach, **counts, "accuracy_percent": 100*counts["correct"]/counts["fields"], "mean_seconds": sum(timings)/len(timings)})
        by_name += [{"approach": approach, "document_type": key[0], "field": key[1], **value, "accuracy_percent": 100*value["correct"]/value["fields"]} for key, value in name_counts.items()]
        by_type += [{"approach": approach, "document_type": key, **value, "accuracy_percent": 100*value["correct"]/value["fields"]} for key, value in type_counts.items()]
        save(args.output / "local_ai" / approach / "results.json", {"version": "1.0", "documents": exports})
    for variant in ("qwen_baseline", "qwen_strict", "python_rag_qwen"):
        stats, seconds, exports = Counter(), 0, []
        for document_id, reference in references.items():
            path = args.output / "local_ai" / variant / (document_id + ".json")
            if not path.exists(): continue
            result = load(path); seconds += result["seconds"]
            stats["llm_requests"] += len(result.get("inference", []))
            expected_by_id = {a["question_id"]: a for a in reference["answers"]}
            for prediction, diag in zip(result["answers"], result["diagnostics"]):
                expected = expected_by_id[prediction["question_id"]]
                answerable = prediction["answerable"] == expected["answerable"]
                value = norm(prediction["value"]) == norm(expected["value"])
                sources = set(prediction["sources"]) == set(expected["sources"])
                is_validation = validation_intent(questions_by_id[prediction["question_id"]]["question"])
                stats["questions"] += 1; stats["answerable_correct"] += answerable
                stats["value_correct"] += value; stats["sources_correct"] += sources
                stats["fully_correct"] += answerable and value and sources
                stats["unsupported_positive_answers"] += prediction["answerable"] and (not expected["answerable"] or not value or not sources)
                # A prose "yes" instead of document_ok is a protocol error, not automatically
                # an invented fact. Keep citation and machine-value defects separate.
                stats["hallucinations"] += prediction["answerable"] and (not expected["answerable"] or
                    (not value and not is_validation) or (is_validation and prediction["value"] in ("document_ok", "missing_fields") and not value))
                stats["validation_format_errors"] += is_validation and prediction["answerable"] and prediction["value"] not in ("document_ok", "missing_fields")
                stats["wrong_source_sets"] += not sources
                stats["fabricated_sources"] += sum(
                    source.startswith("rule:") and get_knowledge().get(source) is None
                    or source.startswith("field:") and source[6:] not in reference["fields"]
                    for source in prediction["sources"])
                stats["false_refusals"] += not prediction["answerable"] and expected["answerable"]
                stats["without_llm"] += not diag.get("llm_called", False)
                stats["rag_calls"] += diag.get("rag_called", False)
                stats["technical_failures"] += diag.get("technical_error", False)
                stats["json_errors"] += any(reason in ("invalid_json", "json_schema", "answer_json_schema", "duplicate_json_key") for reason in diag.get("rejections", []))
                stats["rejected_answers"] += bool(diag.get("rejections"))
                stats["corrected_answers"] += diag.get("corrected", False)
                stats["refusals"] += not prediction["answerable"]
                if not (answerable and value and sources):
                    errors.append({"experiment": variant, "document_id": document_id, "question_id": prediction["question_id"],
                                   "reason": ",".join(diag.get("rejections", [])) or "answer_mismatch",
                                   "expected": json.dumps(expected, ensure_ascii=False), "actual": json.dumps(prediction, ensure_ascii=False)})
            performance += [{"variant": variant, "document_id": document_id, **record} for record in result.get("inference", [])]
            extraction = load(args.output / "local_ai" / "hybrid" / "raw" / (document_id + ".json"))["extraction"]
            exports.append({"document_id": document_id, "document_type": extraction["document_type"], "fields": export_fields(extraction),
                            "validation": export_validation(extraction), "answers": result["answers"]})
        if not stats["questions"]: continue
        qa_rows.append({"approach": variant, **stats, **{name+"_percent":100*stats[name]/stats["questions"] for name in ("answerable_correct", "value_correct", "sources_correct", "fully_correct")}, "mean_seconds": seconds/stats["questions"]})
        export_path = args.output / "local_ai" / variant / "results.json"
        save(export_path, {"version": "1.0", "documents": exports})
        completed = subprocess.run([sys.executable, "-X", "utf8", str(args.dataset / "evaluate.py"), "--reference", str(args.dataset / "reference_results.json"), "--predictions", str(export_path)], capture_output=True, text=True, encoding="utf-8")
        save(export_path.with_name("official_evaluation.json"), {"exit_code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr})
    knowledge = get_knowledge()
    for retrieval in ("bm25", "routed_bm25"):
        relevant, found3, found5, complete3, complete5, count, invalid_sources, extra_sources, retrieved_count = 0, 0, 0, 0, 0, 0, 0, 0, 0
        for question in questions:
            reference = references[question["document_id"]]
            answer = next(a for a in reference["answers"] if a["question_id"] == question["question_id"])
            expected = {s for s in answer["sources"] if s.startswith("rule:")}
            if not expected: continue
            extraction = load(args.output / "local_ai" / "hybrid" / "raw" / (question["document_id"] + ".json"))["extraction"]
            retrieved = knowledge.search(question["question"], 5) if retrieval == "bm25" else retrieve_for_question(question, extraction, knowledge, 5)
            top3, top5 = {item["source"] for item in retrieved[:3]}, {item["source"] for item in retrieved}
            count += 1; relevant += len(expected); found3 += len(expected & top3); found5 += len(expected & top5)
            complete3 += expected <= top3; complete5 += expected <= top5
            invalid_sources += sum(knowledge.get(source) is None for source in top5)
            extra_sources += len(top5 - expected); retrieved_count += len(top5)
        rag_rows.append({"retrieval": retrieval, "questions_with_rule_sources": count,
                         "recall_at_3": found3/max(1,relevant), "recall_at_5": found5/max(1,relevant),
                         "all_sources_at_3": complete3/max(1,count), "all_sources_at_5": complete5/max(1,count),
                         "fabricated_sources": invalid_sources, "non_reference_sources_at_5": extra_sources,
                         "precision_at_5": found5/max(1,retrieved_count)})
    # Explicit columns keep zeros visible, even when Counter has never seen that event.
    field_columns = ["approach","documents","fields","correct","accuracy_percent","missing_values","missed_present_fields","requested_fields","recovered_fields","recovered_correctly","wrong_proposals","rejected_values","llm_documents","technical_failures","mean_seconds"]
    qa_columns = ["approach","questions","answerable_correct_percent","value_correct_percent","sources_correct_percent","fully_correct_percent","hallucinations","unsupported_positive_answers","validation_format_errors","wrong_source_sets","fabricated_sources","false_refusals","without_llm","llm_requests","rag_calls","technical_failures","json_errors","rejected_answers","corrected_answers","refusals","mean_seconds"]
    write_csv(args.output / "field_extraction_comparison.csv", [{c:r.get(c,0) for c in field_columns} for r in field_rows], field_columns)
    write_csv(args.output / "field_accuracy_by_type.csv", by_type)
    write_csv(args.output / "field_accuracy_by_name.csv", by_name)
    write_csv(args.output / "qa_comparison.csv", [{c:r.get(c,0) for c in qa_columns} for r in qa_rows], qa_columns)
    error_columns = ["experiment","document_id","question_id","reason","expected","actual"]
    write_csv(args.output / "qa_errors.csv", [row for row in errors if row["question_id"]], error_columns)
    write_csv(args.output / "field_extraction_errors.csv", [row for row in errors if not row["question_id"]], error_columns)
    write_csv(args.output / "rag_retrieval_metrics.csv", rag_rows)
    columns = sorted(set(k for row in performance for k in row)) or ["status"]
    write_csv(args.output / "inference_performance.csv", performance, columns)
    save(args.output / "local_ai" / "summary.json", {"fields": field_rows, "qa": qa_rows, "rag": rag_rows})
    print(json.dumps({"fields": field_rows, "qa": qa_rows, "rag": rag_rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("extraction", "qa", "evaluate", "all"))
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "benchmark_results")
    parser.add_argument("--variants", default="qwen_baseline,qwen_strict,python_rag_qwen")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.stage in ("extraction", "all"): extraction_experiments(args)
    if args.stage in ("qa", "all"): qa_experiments(args)
    if args.stage in ("evaluate", "all"): evaluate(args)
