"""Decode supported uploads into canonical RGB pages (also used for previews)."""
from io import BytesIO
from math import isfinite
from threading import RLock

from PIL import Image, ImageOps, UnidentifiedImageError
import pypdfium2 as pdfium


MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 12
MAX_IMAGE_PIXELS = 20_000_000
MAX_PDF_SIDE = 2000
# PDFium is not thread-safe. Both Flask validation and background rendering use this lock.
pdf_lock = RLock()


def _pdf(data):
    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        raise ValueError("Не удалось открыть PDF. Проверь файл и отсутствие пароля.") from error
    if not 1 <= len(document) <= MAX_PAGES:
        document.close()
        raise ValueError(f"PDF должен содержать от 1 до {MAX_PAGES} страниц.")
    return document


def validate_document(data: bytes) -> str:
    """Validate actual content, never trust the extension or client MIME type."""
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Документ должен быть не больше 10 МБ.")
    if data.lstrip().startswith(b"%PDF-"):
        with pdf_lock, _pdf(data) as document:
            for index in range(len(document)):
                page = document[index]
                try:
                    width, height = page.get_size()
                    if not all(isfinite(v) and v > 0 for v in (width, height)):
                        raise ValueError("PDF содержит страницу с некорректным размером.")
                finally:
                    page.close()
        return "application/pdf"
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in ("JPEG", "PNG"):
                raise ValueError("Прикрепи документ в формате JPG, PNG или PDF.")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("Изображение слишком большое: максимум 20 мегапикселей.")
            mimetype = Image.MIME[image.format]
            image.verify()
            return mimetype
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("Не удалось открыть изображение документа.") from error


def _rgb(image):
    # Composite alpha onto white, rather than interpreting transparent pixels as ink.
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, "white")
    background.alpha_composite(rgba)
    return background.convert("RGB")


def load_document_pages(data: bytes) -> list[Image.Image]:
    mimetype = validate_document(data)
    if mimetype != "application/pdf":
        with Image.open(BytesIO(data)) as image:
            return [_rgb(ImageOps.exif_transpose(image))]
    pages = []
    with pdf_lock, _pdf(data) as document:
        for index in range(len(document)):
            page = document[index]
            try:
                width, height = page.get_size()
                scale = min(150 / 72, MAX_PDF_SIDE / max(width, height))
                bitmap = page.render(scale=scale)
                try:
                    pages.append(_rgb(bitmap.to_pil()))
                finally:
                    bitmap.close()
            finally:
                page.close()
    return pages


def preview_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()
