"""Export aggregate measurements only; keep documents, OCR and raw answers private."""

import argparse
import csv
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.benchmark_common import load, table


def publish(source, target, revision):
    summary = load(source / "local_ai" / "summary.json")
    fields = {r["approach"]: r for r in summary["fields"]}
    qa = {r["approach"]: r for r in summary["qa"]}
    if (
        set(fields) != {"regex", "hybrid"}
        or set(qa) != {"qwen_baseline", "qwen_strict", "python_rag_qwen"}
        or any(r["documents"] != 30 for r in fields.values())
        or any(r["questions"] != 120 for r in qa.values())
    ):
        raise ValueError("Only the complete 30-document / 120-question experiment can be published")
    environment = load(source / "local_ai" / "qa_environment.json")
    probe_path = source / "local_ai" / "probe_validation.json"
    probes = load(probe_path) if probe_path.exists() else None
    revision = revision or environment.get("protocol_revision")
    if not revision or not re.fullmatch(r"[0-9a-f]{7,40}", revision):
        raise ValueError("Specify --protocol-revision for a legacy run without revision metadata")
    official = {}
    qa_hashes, extraction_hashes = set(), set()
    for variant in qa:
        for path in (source / "local_ai" / variant).glob("open-*.json"):
            qa_hashes.add(load(path)["pipeline_hash"][:64])
        outcome = load(source / "local_ai" / variant / "official_evaluation.json")
        try:
            parsed = json.loads(outcome["stdout"])
        except json.JSONDecodeError:
            parsed = {}
        official[variant] = {
            "exit_code": outcome["exit_code"],
            **{
                k: parsed.get(k)
                for k in (
                    "format_valid",
                    "documents",
                    "questions",
                    "field_accuracy_macro",
                    "validation_exact_match",
                    "question_exact_match",
                )
            },
        }
    for variant in fields:
        for path in (source / "local_ai" / variant / "raw").glob("open-*.json"):
            extraction_hashes.add(load(path)["pipeline_hash"])
    if len(qa_hashes) != 1 or len(extraction_hashes) != 1:
        raise ValueError("Mixed protocol hashes: inspect the run before publication")
    target.mkdir(parents=True, exist_ok=True)
    # These six files contain counts, schema identifiers and timing aggregates only.
    for name in (
        "field_extraction_comparison.csv",
        "field_accuracy_by_type.csv",
        "field_accuracy_by_name.csv",
        "qa_comparison.csv",
        "rag_retrieval_metrics.csv",
    ):
        shutil.copyfile(source / name, target / name)
    with (source / "qa_errors.csv").open(encoding="utf-8-sig", newline="") as stream:
        counts = Counter((r["experiment"], r["reason"]) for r in csv.DictReader(stream))
    with (target / "qa_error_counts.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["experiment", "reason", "questions"])
        writer.writerows([*key, count] for key, count in sorted(counts.items()))
    public = {
        "protocol_revision": revision,
        "protocol_hashes": {"extraction": sorted(extraction_hashes), "qa": sorted(qa_hashes)},
        "model_probes": probes,
        "fields": summary["fields"],
        "qa": summary["qa"],
        "rag": summary["rag"],
        "official_evaluation": official,
        "model": {
            k: environment["model"][k]
            for k in (
                "engine_release",
                "model_repository",
                "model_revision",
                "model_file",
                "model_sha256",
                "quantization",
            )
        },
        "runtime": {k: environment["runtime"][k] for k in ("backend", "threads")},
    }
    (target / "summary.json").write_text(
        json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Измерения на учебной выборке",
        "",
        "Полный реальный прогон: **30 синтетических документов, 120 вопросов для каждого QA-варианта**. "
        "Набор использовался при разработке; это не независимая оценка реальных студенческих документов. "
        "PNG/PDF — представления одних документов, а не 60 независимых примеров.",
        "",
        "## Извлечение полей",
        "",
    ]
    lines += table(
        ["Подход", "Правильно", "Micro accuracy", "Среднее, с/документ", "Документов с LLM", "Восстановлено"],
        [
            [
                name,
                f"{r['correct']}/{r['fields']}",
                f"{r['accuracy_percent']:.2f}%",
                f"{r['mean_seconds']:.3f}",
                r.get("llm_documents", 0),
                r.get("recovered_fields", 0),
            ]
            for name, r in fields.items()
        ],
    )
    lines += [
        "Один оставшийся PNG-конфликт — латинские/кириллические символы номера `open-009`. "
        "Fallback для отсутствующих полей сохраняет уже найденный номер. Если поле действительно пусто, "
        "null является правильным результатом. Нулевое число восстановлений не означает улучшения точности.",
        "",
        "## Ответы",
        "",
    ]
    lines += table(
        ["Подход", "Exact QA", "Среднее, с/вопрос", "LLM-запросов", "Ложные отказы", "Технические ошибки"],
        [
            [
                name,
                f"{r['fully_correct']}/{r['questions']} ({r['fully_correct_percent']:.2f}%)",
                f"{r['mean_seconds']:.4f}",
                r.get("llm_requests", 0),
                r.get("false_refusals", 0),
                r.get("technical_failures", 0),
            ]
            for name, r in qa.items()
        ],
    )
    lines += [
        "Exact QA сравнивает **answerable, value и множество sources**. Смысл каждого предложения "
        "свободного ответа автоматически не доказан. Два генеративных варианта оценены по сырым ответам; "
        "пользователю такие ответы без проверки не выдаются. Baseline — заново определённый простой промпт, "
        "не историческая версия Qwen. Время генеративного QA амортизировано: пакет из четырёх вопросов "
        "делится на четыре. Это не задержка одиночного вопроса.",
        "",
        "Варианты, не прошедшие официальный формат: "
        + (", ".join(name for name, result in official.items() if not result.get("format_valid")) or "нет")
        + ". Для них Exact QA выше является диагностической метрикой собственного постобработчика, "
        "не официальным баллом. Например, корректная JSON-строка `VALIDATION.missing_fields` "
        "не является допустимой ссылкой `field:*` или `rule:*`. Сырые ответы не исправлялись для оценки.",
        "",
        "Столбец hallucinations учитывает выявленные ошибки фактического value и необоснованные ответы. "
        "Ошибки формата статуса вынесены в validation_format_errors. Полная ручная разметка каждого утверждения "
        "свободного текста не выполнялась.",
        "",
        "Если гибрид отвечает без LLM на всех вопросах набора, его QA-оценка проверяет Python и правила, "
        "а не качество сложной генерации Qwen. Отдельные синтетические реальные пробы модели описаны "
        "в локальном полном отчёте; они использовались при разработке и не добавлены к accuracy.",
        "",
        (
            f"Отдельные реальные пробы после рефакторинга: {probes['scenarios'] - len(probes['failed'])}/"
            f"{probes['scenarios']} целевых результатов. Неуспешные сценарии: {', '.join(probes['failed']) or 'нет'}. "
            "Отказ принять неверное значение не считается успешным восстановлением. Результаты сохранены; "
            "повторы для получения зелёного результата не выполнялись."
            if probes
            else "Дополнительные пробы в этом экспорте не указаны."
        ),
        "",
        "## Поиск правил",
        "",
    ]
    lines += table(
        ["Поиск", "Вопросов", "Recall@3", "Recall@5", "Все источники @5"],
        [
            [
                r["retrieval"],
                r["questions_with_rule_sources"],
                f"{100 * r['recall_at_3']:.2f}%",
                f"{100 * r['recall_at_5']:.2f}%",
                f"{100 * r['all_sources_at_5']:.2f}%",
            ]
            for r in summary["rag"]
        ],
    )
    lines += [
        "`routed_bm25` дополнительно поднимает пункты, установленные детерминированной проверкой "
        "комплектности. Эти ID берутся из рабочей схемы, а не из эталона. Полная RAG-таблица содержит "
        "также дополнительные источники и Precision@5.",
        "",
        "## Официальный оценщик",
        "",
    ]
    lines += table(
        ["Вариант", "Exit code", "Формат", "Macro fields", "Validation exact", "QA exact"],
        [
            [
                name,
                r["exit_code"],
                r["format_valid"],
                r["field_accuracy_macro"],
                r["validation_exact_match"],
                r["question_exact_match"],
            ]
            for name, r in official.items()
        ],
    )
    model = public["model"]
    lines += [
        "## Воспроизводимость и границы",
        "",
        f"Протокол измерения зафиксирован в коммите `{revision}` до организационного рефакторинга. "
        "Промпты и алгоритм не менялись во время генерации. Последующие перенос чистых правил, исправление "
        "импортов и форматирование проверены регрессионными тестами; времена относятся к измеренному протоколу.",
        "",
        f"Windows, Intel Core i5-1135G7, около 16 ГБ RAM; CPU llama.cpp `{model['engine_release']}`, 4 потока, "
        "один слот, контекст 4096; температура 0, seed 42, no thinking. Сервер сохранял веса между запросами. "
        "Отдельные пробы Vulkan не включены в эти метрики.",
        "",
        f"Модель: `{model['model_repository']}`, ревизия `{model['model_revision']}`, "
        f"`{model['model_file']}`. SHA256: `{model['model_sha256']}`.",
        "",
        "Предыдущая проверка CV до интеграции AI: PNG 279/280, PDF 280/280, классификация и комплектность "
        "30/30, QA 120/120. Это те же документы и другие условия запуска; сравнивать времена как ускорение нельзя.",
        "",
        "В Git включены только агрегаты. Полные документы, OCR, значения, ответы модели, сравнения с эталоном "
        "и временные GPU-пробы остаются локально в `benchmark_results/`. Там же находятся семь запрошенных CSV, "
        "сырой `inference_performance.csv`, полный `report.md` и журналы официального оценщика. "
        "[Команды воспроизведения](../../README.md#воспроизведение-бенчмарков).",
        "",
        "Проценты не доказывают подлинность подписи, правомерность решения и отсутствие галлюцинаций на "
        "произвольных вопросах. Новые правила, реальные фото и другие макеты требуют собственной проверки.",
        "",
    ]
    (target / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Published aggregate documentation: {target}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "benchmark_results")
    parser.add_argument("--target", type=Path, default=ROOT / "docs" / "benchmarks")
    parser.add_argument("--protocol-revision")
    args = parser.parse_args()
    if args.protocol_revision and not re.fullmatch(r"[0-9a-f]{7,40}", args.protocol_revision):
        parser.error("--protocol-revision must be a Git commit hash")
    publish(args.source, args.target, args.protocol_revision)
