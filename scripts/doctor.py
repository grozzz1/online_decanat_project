"""Read-only local readiness checks. Never sends a Telegram message or document."""

import argparse
import json
import sys
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytesseract
from PIL import Image, ImageDraw

from utils.config import get_admin_log_chat_id, get_dean_chat_id, get_tesseract_command, get_token
from utils.knowledge import get_knowledge
from utils.local_ai import get_local_ai


def check(*, ocr=False, ai=False, skip_telegram_config=False):
    result = {"python": sys.version.split()[0], "checks": {}}
    checks = result["checks"]
    try:
        import main  # noqa: F401 -- verify all entry-point imports without starting it

        checks["imports"] = {"ok": True}
    except Exception as error:
        checks["imports"] = {"ok": False, "error_type": type(error).__name__}
    result["packages"] = {
        name: version(name)
        for name in (
            "Flask",
            "pyTelegramBotAPI",
            "pytesseract",
            "Pillow",
            "opencv-python-headless",
            "numpy",
            "pypdfium2",
            "jsonschema",
            "aiohttp",
            "python-dotenv",
        )
    }
    if not skip_telegram_config:
        try:
            get_token()
            get_dean_chat_id()
            get_admin_log_chat_id()
            checks["telegram_configuration"] = {"ok": True, "network_checked": False}
        except ValueError:
            checks["telegram_configuration"] = {
                "ok": False,
                "help": "Fill BOT_TOKEN, DEAN_CHAT_ID and ADMIN_LOG_CHAT_ID in .env",
            }
    try:
        knowledge = get_knowledge()
        knowledge.ensure()
        checks["knowledge"] = {"ok": bool(knowledge.get("rule:1.3")), "chunks": len(knowledge.chunks)}
    except Exception as error:
        checks["knowledge"] = {"ok": False, "error_type": type(error).__name__}
    if ocr:
        try:
            pytesseract.pytesseract.tesseract_cmd = get_tesseract_command()
            languages = pytesseract.get_languages()
            with Image.new("RGB", (600, 120), "white") as image:
                ImageDraw.Draw(image).text((15, 30), "DEAN 123", fill="black", font_size=36)
                recognized = pytesseract.image_to_string(image, lang="rus+eng", config="--psm 6", timeout=30)
            checks["ocr"] = {
                "ok": {"rus", "eng"}.issubset(languages) and "123" in recognized,
                "version": str(pytesseract.get_tesseract_version()),
                "languages": languages,
            }
        except Exception as error:
            checks["ocr"] = {"ok": False, "error_type": type(error).__name__}
    if ai:
        try:
            health = get_local_ai().health()
            checks["local_ai"] = {"ok": bool(health.get("available")), **health}
        except Exception as error:
            checks["local_ai"] = {"ok": False, "error_type": type(error).__name__}
    result["ok"] = all(item["ok"] for item in checks.values())
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--ai", action="store_true")
    parser.add_argument("--skip-telegram-config", action="store_true")
    args = parser.parse_args()
    readiness = check(ocr=args.ocr, ai=args.ai, skip_telegram_config=args.skip_telegram_config)
    print(json.dumps(readiness, ensure_ascii=False, indent=2))
    raise SystemExit(0 if readiness["ok"] else 1)
