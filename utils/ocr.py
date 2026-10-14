from asyncio import to_thread
from pathlib import Path
from secrets import token_hex
from os import getenv
import pytesseract
from utils.config import get_tesseract_command
from utils.documents import load_document_pages, preview_bytes
from utils.document_cv import analyze_document_visuals, save_debug_image, summarize_visuals
from utils.validation import validate_document_fields, validate_document_visuals
from utils.document_fields import empty_extraction, extract_document_fields, normalize_document_type
from utils.document_classification import classify_document
from utils.document_rules import preliminary_check


def recognize_text(document) -> str:
    pytesseract.pytesseract.tesseract_cmd = get_tesseract_command()

    pages = load_document_pages(document.getvalue())
    try:
        return "\n\n".join(pytesseract.image_to_string(image, lang="rus+eng", config="--psm 6", timeout=60).strip()
                            for image in pages)
    finally:
        for image in pages:
            image.close()


async def recognize_text_async(document) -> str:
    return await to_thread(recognize_text, document)


def _ocr_words(image):
    data = pytesseract.image_to_data(image, lang="rus+eng", config="--psm 11",
                                     output_type=pytesseract.Output.DICT, timeout=60)
    words = []
    for index, text in enumerate(data["text"]):
        if not text.strip():
            continue
        x, y = data["left"][index], data["top"][index]
        words.append({"text": text, "confidence": float(data["conf"][index]),
                      "bbox": [x, y, x + data["width"][index], y + data["height"][index]],
                      "line": [data["block_num"][index], data["par_num"][index], data["line_num"][index]]})
    return words


def analyze_document(document, application_type="", debug_dir=None, *, use_ai=True):
    """OCR + CV for image/PDF; preview pages and boxes share the canonical orientation."""
    pytesseract.pytesseract.tesseract_cmd = get_tesseract_command()
    images = load_document_pages(document.getvalue())
    debug_dir = debug_dir or getenv("DOCUMENT_CV_DEBUG_DIR")
    texts, detections, pages, previews, warnings = [], [], [], [], []
    processing_errors = []
    debug_id = token_hex(8)
    try:
        for number, source in enumerate(images, 1):
            image, rotation = source, 0
            try:
                osd = pytesseract.image_to_osd(source, output_type=pytesseract.Output.DICT, timeout=10)
                if float(osd.get("orientation_conf", 0)) >= 5:
                    rotation = int(osd.get("rotate", 0)) % 360
                    if rotation:
                        image = source.rotate(-rotation, expand=True)
            except (pytesseract.TesseractError, RuntimeError):
                # Sparse documents or missing osd.traineddata must not break existing OCR.
                warnings.append(f"Страница {number}: ориентация не определена автоматически.")
            try:
                text = pytesseract.image_to_string(image, lang="rus+eng", config="--psm 6", timeout=60).strip()
                texts.append(text if len(images) == 1 else f"Страница {number}\n{text}")
                words = []
                try:
                    words = _ocr_words(image)
                except (pytesseract.TesseractError, RuntimeError) as error:
                    warnings.append(f"Страница {number}: координаты OCR недоступны ({type(error).__name__}).")
                    processing_errors.append({"stage": "ocr_coordinates", "code": type(error).__name__})
                metadata = {"page": number, "width": image.width, "height": image.height,
                            "rotation": rotation, "ocr_words": words, "analysis_status": "complete"}
                try:
                    debug_path = Path(debug_dir) / f"{debug_id}-page-{number}.png" if debug_dir else None
                    visuals = analyze_document_visuals(image, words, number)
                    detections.extend(visuals["detections"])
                    if debug_path:
                        try:
                            save_debug_image(image, visuals, debug_path, number)
                        except OSError:
                            warnings.append(f"Страница {number}: не удалось сохранить отладочное изображение.")
                except Exception as error:
                    # CV is supplementary: forwarding an otherwise valid document still works.
                    metadata["analysis_status"] = "unavailable"
                    warnings.append(f"Страница {number}: визуальный анализ недоступен ({type(error).__name__}).")
                    processing_errors.append({"stage": "cv", "code": type(error).__name__})
                pages.append(metadata)
                previews.append(preview_bytes(image))
            finally:
                if image is not source:
                    image.close()
    finally:
        for image in images:
            image.close()
    visuals = summarize_visuals(detections, pages, warnings)
    if any(page["analysis_status"] != "complete" for page in pages):
        for key in ("stamp", "signature", "student_signature"):
            if not visuals[key]["detected"]:
                visuals[key]["status"] = "unknown"
    result = reprocess_document("\n\n".join(texts), visuals, application_type, processing_errors, use_ai=use_ai)
    result["previews"] = previews
    return result


def reprocess_document(recognized, visuals, selected_type="", processing_errors=None, *, use_selected_type=False, use_ai=True, cached_extraction=None):
    classification = classify_document(recognized, visuals.get("pages"))
    selected = normalize_document_type(selected_type)
    detected = classification["detected_document_type"]
    document_type = selected if use_selected_type and selected else normalize_document_type(detected) or selected
    matches = detected == selected if detected != "unknown" and selected else None
    extraction = None
    validation = validate_document_visuals(document_type, visuals)
    if document_type:
        try:
            reuse = (cached_extraction and cached_extraction.get("document_type") == document_type
                     and not any(error.get("stage") == "extraction" for error in cached_extraction.get("errors", [])))
            if reuse:
                from copy import deepcopy
                extraction = deepcopy(cached_extraction)
                extraction["errors"] = []
            else:
                extraction = extract_document_fields(recognized, document_type, visuals)
            if use_ai and not reuse:
                try:
                    from utils.hybrid_extraction import supplement_fields
                    extraction = supplement_fields(extraction, recognized)
                except Exception as error:
                    # A broken optional AI configuration must not erase valid Regex output.
                    extraction["hybrid"] = {"status": type(error).__name__, "llm_called": False,
                                            "accepted": [], "rejected": {}}
        except Exception as error:
            extraction = empty_extraction(document_type, [{"stage": "extraction", "code": type(error).__name__}])
            extraction["field_statuses"] = {name: "unknown" for name in extraction["fields"]}
        extraction["errors"].extend(processing_errors or [])
        extraction.update(classification, selected_document_type=selected, document_type_matches_selection=matches)
        validation["fields"] = validate_document_fields(extraction)
        validation["completeness"] = preliminary_check(extraction)
        validation["manual_review"] = validation["manual_review"] or bool(validation["fields"]["issues"]) or bool(extraction["errors"])
    return {"recognized": recognized, "ocr_text": recognized, "visuals": visuals, "validation": validation,
            "selected_document_type": selected, **classification, "document_type_matches_selection": matches,
            "document_type": document_type, "fields": extraction["fields"] if extraction else {}, "extraction": extraction}


async def analyze_document_async(document, application_type="", debug_dir=None):
    return await to_thread(analyze_document, document, application_type, debug_dir)


if __name__ == "__main__":
    import argparse
    import json
    from io import BytesIO

    parser = argparse.ArgumentParser(description="OCR и визуальные признаки печатей/подписей в JPG, PNG, PDF")
    parser.add_argument("document", type=Path)
    parser.add_argument("--debug-dir", type=Path)
    parser.add_argument("--output", type=Path, help="Сохранить JSON, по умолчанию вывести в терминал")
    args = parser.parse_args()
    result = analyze_document(BytesIO(args.document.read_bytes()), debug_dir=args.debug_dir)
    result.pop("previews")
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    else:
        print(serialized)
