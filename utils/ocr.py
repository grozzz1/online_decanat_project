from asyncio import to_thread
from pathlib import Path
from secrets import token_hex
from os import getenv
import pytesseract
from utils.config import get_tesseract_command
from utils.documents import load_document_pages, preview_bytes
from utils.document_cv import analyze_document_visuals, save_debug_image, summarize_visuals
from utils.validation import validate_document_visuals


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


def analyze_document(document, application_type="", debug_dir=None):
    """OCR + CV for image/PDF; preview pages and boxes share the canonical orientation."""
    pytesseract.pytesseract.tesseract_cmd = get_tesseract_command()
    images = load_document_pages(document.getvalue())
    debug_dir = debug_dir or getenv("DOCUMENT_CV_DEBUG_DIR")
    texts, detections, pages, previews, warnings = [], [], [], [], []
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
    return {"recognized": "\n\n".join(texts), "visuals": visuals, "previews": previews,
            "validation": validate_document_visuals(application_type, visuals)}


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
