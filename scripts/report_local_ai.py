"""Build the human report exclusively from saved measurements; never run inference."""

import argparse
import csv
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.benchmark_common import cell, load, table


def generate(output):
    summary = load(output / "local_ai" / "summary.json")
    fields = {row["approach"]: row for row in summary["fields"]}
    qa = {row["approach"]: row for row in summary["qa"]}
    env = load(output / "local_ai" / "qa_environment.json")
    model = env["model"]
    official = {}
    for name in qa:
        outcome = load(output / "local_ai" / name / "official_evaluation.json")
        try:
            checked = json.loads(outcome["stdout"])
        except json.JSONDecodeError:
            checked = {}
        official[name] = {"exit_code": outcome["exit_code"], **checked}
    complete = (
        all(row.get("questions") == 120 for row in qa.values())
        and len(qa) == 3
        and all(row["documents"] == 30 for row in fields.values())
    )
    tests = (output / "local_ai" / "tests.txt").read_text(encoding="utf-8", errors="replace")
    count = re.search(r"Ran (\d+) tests", tests)

    def field_accuracy(name):
        if name not in fields:
            return "N/A"
        return f"{fields[name]['accuracy_percent']:.2f}% ({fields[name]['correct']}/{fields[name]['fields']})"

    def qa_accuracy(name):
        if name not in qa:
            return "N/A"
        return (
            f"{qa[name]['fully_correct_percent']:.2f}% ({qa[name]['fully_correct']}/{qa[name]['questions']})"
        )

    def qa_time(name):
        return f"{qa[name]['mean_seconds']:.4f}" if name in qa else "N/A"

    lines = [
        "# Локальные Qwen3-4B, гибридное извлечение и ответы по источникам",
        "",
        f"Отчёт сформирован {datetime.now():%d.%m.%Y %H:%M}. "
        + (
            "Полный прогон: 30 документов, 120 вопросов для каждого из трёх вариантов QA."
            if complete
            else "ПРОГОН НЕ ПОЛОН: число завершённых документов/вопросов указано в CSV; показатели не относятся ко всей выборке."
        ),
        "",
        "Все исходные файлы предоставленного набора найдены. Это 30 синтетических документов открытой выборки; "
        "данные использовались при разработке, поэтому показатели не являются независимой оценкой на реальных студенческих документах.",
        "",
        "## Сравнение",
        "",
    ]
    rows = [
        [
            "Regex + прежний CV",
            field_accuracy("regex"),
            "N/A",
            f"{fields['regex']['mean_seconds']:.3f} с/документ",
        ],
        [
            "Regex → Qwen + проверка",
            field_accuracy("hybrid"),
            "N/A",
            f"{fields['hybrid']['mean_seconds']:.3f} с/документ",
        ],
        [
            "Qwen QA: простой baseline_v1",
            "N/A",
            qa_accuracy("qwen_baseline"),
            qa_time("qwen_baseline") + " с/вопрос",
        ],
        ["Qwen QA: строгий промпт", "N/A", qa_accuracy("qwen_strict"), qa_time("qwen_strict") + " с/вопрос"],
        [
            "Python + BM25/RAG + Qwen + проверка",
            field_accuracy("hybrid"),
            qa_accuracy("python_rag_qwen"),
            qa_time("python_rag_qwen") + " с/вопрос",
        ],
    ]
    lines += table(["Подход", "Поля, micro", "Полностью правильные QA", "Среднее время"], rows)
    lines += [
        "Время извлечения — два реальных прохода OCR/CV/извлечения на каждом PNG, без подстановки эталонных полей. "
        "Два варианта Qwen QA получают одинаковые данные и источники, по 4 вопроса на документ; время QA делится на 4. "
        "Это амортизированное время пакета, а не задержка одиночного пользовательского запроса. Python отвечает по одному вопросу. "
        "Замеры сделаны на рабочем ПК без изоляции фоновой нагрузки; абсолютное время нельзя переносить на другое оборудование.",
        "",
        "Ранее в проекте уже был детерминированный benchmark-обработчик с 100% ответов на этой открытой выборке; "
        "[предыдущий отчёт](pre_ai_report.md) сохранён. Исторического промпта/инференса Qwen не существовало: "
        "baseline_v1 создан и реально выполнен в этой доработке. Поэтому сравнение с ним не выдаётся за улучшение старой Python-точности.",
        "",
        "## Извлечение полей",
        "",
    ]
    lines += table(
        [
            "Подход",
            "Документов с LLM",
            "Запрошено полей",
            "Восстановлено / правильно",
            "Неверных предложений",
            "Отклонено",
            "Пропущено существующих текстовых полей",
        ],
        [
            [
                name,
                f"{row.get('llm_documents', 0)}/{row['documents']}",
                row.get("requested_fields", 0),
                f"{row.get('recovered_fields', 0)} / {row.get('recovered_correctly', 0)}",
                row.get("wrong_proposals", 0),
                row.get("rejected_values", 0),
                row.get("missed_present_fields", 0),
            ]
            for name, row in fields.items()
        ],
    )
    lines += [
        "В этой выборке Regex уже находит все присутствующие текстовые поля. Пропуски, переданные Qwen, в основном "
        "соответствуют намеренно незаполненным местам: правильный результат для них — null. Отсутствие восстановления "
        "не считается ошибкой модели, когда значения в документе нет. Уже найденные значения и графические отметки Qwen не меняет.",
        "",
        "Оставшаяся ошибка — **open-009, document_number**: OCR/Regex возвращают латинское `OB-1009`, "
        "эталон — кириллическое `ОБ-1009`. Значение имеет допустимый формат, поэтому восстановление пропусков его не заменяет. "
        "Точность полей на основной выборке от добавления Qwen не выросла. Подробности — "
        "[field_accuracy_by_type.csv](field_accuracy_by_type.csv), [field_accuracy_by_name.csv](field_accuracy_by_name.csv), "
        "[field_extraction_errors.csv](field_extraction_errors.csv).",
        "",
        "## Ответы на 120 вопросов",
        "",
        "Официальный оценщик: "
        + "; ".join(
            f"{name}: exit={r['exit_code']}, format_valid={r.get('format_valid')}"
            for name, r in official.items()
        ),
        "Если format_valid=false, проценты ниже являются только диагностическим сравнением сохранённых "
        "answerable/value/sources, не официальным баллом. Корректный JSON и JSON Schema клиента не гарантируют "
        "допустимые источники официального формата. Сырые ссылки не исправлялись для улучшения оценки. "
        "stdout/stderr и коды сохранены рядом с соответствующими results.json.",
        "",
    ]
    lines += table(
        [
            "Подход",
            "answerable",
            "value",
            "sources",
            "Полностью",
            "Ложные отказы",
            "LLM-запросов",
            "Без LLM",
            "Ошибки JSON / технические",
        ],
        [
            [
                name,
                f"{r['answerable_correct_percent']:.2f}%",
                f"{r['value_correct_percent']:.2f}%",
                f"{r['sources_correct_percent']:.2f}%",
                f"{r['fully_correct_percent']:.2f}%",
                r.get("false_refusals", 0),
                r.get("llm_requests", 0),
                r.get("without_llm", 0),
                f"{r.get('json_errors', 0)} / {r.get('technical_failures', 0)}",
            ]
            for name, r in qa.items()
        ],
    )
    lines += table(
        [
            "Подход",
            "Ошибочные фактические положительные ответы*",
            "Все неподтверждённые положительные",
            "Неверные наборы ссылок",
            "Выдуманные ссылки",
            "Ошибки формата комплектности",
        ],
        [
            [
                name,
                r.get("hallucinations", 0),
                r.get("unsupported_positive_answers", 0),
                r.get("wrong_source_sets", 0),
                r.get("fabricated_sources", 0),
                r.get("validation_format_errors", 0),
            ]
            for name, r in qa.items()
        ],
    )
    lines += [
        "*Счётчик `hallucinations`: положительный ответ при эталонном отказе, неверное значение фактического поля/правила "
        "или противоположный машинный статус комплектности. Произвольное «да» вместо `document_ok` считается ошибкой протокола, "
        "а не автоматически выдуманным фактом. Более широкий `unsupported_positive_answers` включает также несоответствие "
        "значения/источников формату эталона. Корректность каждого слова свободного текста `answer` оценщик не доказывает; "
        "кроме отдельных разобранных примеров ручная разметка всех утверждений не выполнялась.",
        "",
        "Сырые ответы двух промптов сохраняются только для эксперимента. В рабочем пути Python не даёт модели подменять "
        "поля или комплектность. Для сложных вопросов Qwen выбирает существующие источники; код возвращает их полные проверенные "
        "цитаты, а не непроверенные свободные объяснения. Одна повторная генерация допустима после отказа валидатора. "
        "Числа отказов, отклонений и исправлений — [qa_comparison.csv](qa_comparison.csv).",
        "",
        "## Примеры ошибок QA",
        "",
    ]
    error_path = output / "qa_errors.csv"
    with error_path.open(encoding="utf-8-sig", newline="") as source:
        errors = list(csv.DictReader(source))
    for row in errors[:6]:
        expected, actual = json.loads(row["expected"]), json.loads(row["actual"])
        lines += [
            f"- `{row['experiment']}`, **{row['question_id']}**: value `{cell(actual['value'])}` вместо `{cell(expected['value'])}`; "
            f"источники `{cell(actual['sources'])}` вместо `{cell(expected['sources'])}`. Проверка: `{row['reason']}`."
        ]
    if not errors:
        lines.append("Несовпадений answerable/value/sources не обнаружено.")
    lines += [
        "",
        'Полный список — [qa_errors.csv](qa_errors.csv). Строка `"null"`, чужая дата, неверный question_id, '
        "выдуманные ключи/пункты и невалидный JSON отдельно проверяются автоматическими негативными тестами. "
        "Эти намеренно подставленные ошибки не выдаются за ошибки, реально сгенерированные Qwen в основном прогоне.",
        "",
        "## Поиск правил",
        "",
    ]
    lines += table(
        [
            "Поиск",
            "Вопросов с rule-эталоном",
            "Recall@3",
            "Recall@5",
            "Все нужные источники @3",
            "Все @5",
            "Несуществующие ссылки",
        ],
        [
            [
                r["retrieval"],
                r["questions_with_rule_sources"],
                f"{100 * r['recall_at_3']:.2f}%",
                f"{100 * r['recall_at_5']:.2f}%",
                f"{100 * r['all_sources_at_3']:.2f}%",
                f"{100 * r['all_sources_at_5']:.2f}%",
                r["fabricated_sources"],
            ]
            for r in summary["rag"]
        ],
    )
    lines += [
        "`bm25` — обычный текстовый поиск. `routed_bm25` дополнительно поднимает конкретные пункты, которые уже "
        "установлены Python-проверкой комплектности по извлечённым полям. Это не доступ к эталону: ID берутся из рабочей "
        "схемы правил. Метрики вычисляются после выполнения по эталонным источникам, с проверкой полного набора при нескольких "
        "обязательных пунктах. Дополнительные неэталонные результаты и Precision@5 сохранены отдельно в "
        "[rag_retrieval_metrics.csv](rag_retrieval_metrics.csv); неэталонный дополнительный пункт не обязательно ложен. "
        "Retrieval не смешивается с точностью генерации.",
        "",
        "## Дополнительные реальные проверки",
        "",
    ]
    stress_path = output / "local_ai" / "stress_results.json"
    if stress_path.exists():
        stress = load(stress_path)
        lines += table(
            ["Синтетический сценарий", "Результат"],
            [
                [
                    s["case"],
                    str(s["passed"])
                    if "passed" in s
                    else f"answerable={s['result']['answerable']}, sources={s['result']['sources']}, rejected={s['diagnostics']['rejections']}",
                ]
                for s in stress
            ],
        )
        lines += [
            "Это отдельные демонстрационные проверки, использованные при разработке, а не дополнительные документы "
            "основного бенчмарка и не независимая оценка. Таблица отражает фактический результат каждого сценария: "
            "False означает, что ожидаемое восстановление не получилось. Некорректные календарные даты "
            "отклоняются с сохранением null; правильный формат JSON не делает дату достоверной. "
            "В сценариях отсутствующих значений проверяется сохранение null; "
            "дата выдачи не подставляется вместо начала обучения. Сырые результаты — "
            "[stress_results.json](local_ai/stress_results.json).",
            "",
        ]
    lines += [
        f"Полный автоматический прогон: **{count[1] if count else 'см. журнал'} тестов**, "
        "[журнал](local_ai/tests.txt). Браузерные проверки: извлечённые поля/происхождение, AI-ответ и источники, "
        "отказ, мобильный экран, прежние сценарии смены типа/отправки, PDF и масштабирование рамок.",
        "",
        "## Оборудование и воспроизводимость",
        "",
        "Измерения выполнены на Windows, Intel Core i5-1135G7, 16 ГБ RAM, CPU-сервер llama.cpp (4 потока, один слот, "
        "контекст 4096). Vulkan на Intel Iris Xe также установлен, устройство обнаружено и модель успешно запущена; "
        "пробные GPU-ответы сохранены в `local_ai/gpu_probe` и не включены в итоговые QA-метрики. Итоговые три подхода "
        "используют один и тот же CPU-сервер.",
        "",
        f"Модель: `{model['model_file']}`. Репозиторий `{model['model_repository']}`, ревизия `{model['model_revision']}`. "
        f"SHA256: `{model['model_sha256']}`. llama.cpp `{model['engine_release']}`; температура 0, seed 42, thinking отключён. "
        "Сервер не перезагружает веса для каждого документа; запросы ограничены очередью и тайм-аутами.",
        "",
        "Контрольные точки незавершённого прогона переиспользуются только при совпадении хеша пайплайна и входного результата. "
        "Промпты, параметры, команды сервера, сырые ответы и времена сохранены в `local_ai/`; чтение эталонов допускается "
        "только в стадии evaluate. Официальный `evaluate.py` вызывается отдельно для трёх QA-вариантов; его stdout/stderr "
        "и exit code лежат рядом с соответствующим `results.json`.",
        "",
        "## Что осталось ограничением",
        "",
        "- Рост точности полей на этих 30 документах не получен; при истинно пустых полях правильнее сохранить null.",
        "- Латинский номер open-009 остаётся ошибкой OCR, которую fallback для пропусков намеренно не меняет.",
        "- Нормативная база небольшая и вымышленная. BM25 использует простую нормализацию; сложные перефразировки могут терять релевантные пункты.",
        "- Проверка цитат не доказывает универсальную семантическую релевантность. Поэтому рабочая генерация ограничена подбором проверенных фактов, а юридические/учебные решения не автоматизируются.",
        "- При изменении обязательных требований нужно обновить согласованную Python-схему; переиндексация не переписывает бизнес-правила сама.",
        "- Реальные сканы, почерки, шумный OCR, другие языки и произвольные новые вопросы требуют отдельной размеченной проверки. Полное устранение галлюцинаций не заявляется.",
        "",
        "## Файлы и команды",
        "",
        "Установка и точные команды: [LOCAL_AI.md](../docs/LOCAL_AI.md). Параметры: [.env.example](../.env.example).",
        "",
        "CSV: [field_extraction_comparison.csv](field_extraction_comparison.csv), "
        "[field_accuracy_by_type.csv](field_accuracy_by_type.csv), [field_accuracy_by_name.csv](field_accuracy_by_name.csv), "
        "[qa_comparison.csv](qa_comparison.csv), [qa_errors.csv](qa_errors.csv), "
        "[rag_retrieval_metrics.csv](rag_retrieval_metrics.csv), [inference_performance.csv](inference_performance.csv).",
        "",
        "Основные изменения: `utils/local_ai.py`, `hybrid_extraction.py`, `knowledge.py`, `question_answering.py`, "
        "`document_context.py`, `document_fields.py`, `document_rules.py`, `ocr.py`, `bot.py`; "
        "`handlers/requests.py`; `webapp/app.py`, `service.py`, `store.py`, шаблон и JS/CSS; "
        "скрипты установки/запуска/индексации/измерений, автоматические и браузерные тесты, runtime-зависимость jsonschema.",
        "",
    ]
    report = output / "report.md"
    backup = output / "pre_ai_report.md"
    if report.exists() and not backup.exists():
        shutil.copy2(report, backup)
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written: {report}; complete={complete}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "benchmark_results")
    generate(parser.parse_args().output)
