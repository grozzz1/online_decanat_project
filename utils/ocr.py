from asyncio import to_thread
from io import BytesIO
from PIL import Image
import pytesseract
from utils.config import get_tesseract_command


def recognize_text(document) -> str:
    pytesseract.pytesseract.tesseract_cmd = get_tesseract_command()

    with BytesIO(document.getvalue()) as photo, Image.open(photo) as image:
        return pytesseract.image_to_string(
            image,
            lang="rus+eng",
            config="--psm 6",
            timeout=60,
        ).strip()


async def recognize_text_async(document) -> str:
    return await to_thread(recognize_text, document)
