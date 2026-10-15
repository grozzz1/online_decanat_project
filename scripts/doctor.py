import argparse
import asyncio
import json
import sys
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def _telegram_health():
    from telebot.async_telebot import AsyncTeleBot

    from utils.config import get_token

    bot = AsyncTeleBot(get_token())
    try:
        identity, webhook = await asyncio.wait_for(
            asyncio.gather(bot.get_me(), bot.get_webhook_info()), timeout=15
        )
        return {
            "ok": not bool(webhook.url),
            "bot_username": identity.username,
            "webhook_enabled": bool(webhook.url),
        }
    finally:
        await bot.close_session()


def check(*, ocr=False, ai=False, telegram=False, skip_telegram_config=False):
    result = {"python": sys.version.split()[0], "checks": {}}
    checks = result["checks"]
    packages = result["packages"] = {}
    missing = []
    for name in (
        "Flask",
        "waitress",
        "SQLAlchemy",
        "pyTelegramBotAPI",
        "pytesseract",
        "Pillow",
        "opencv-python-headless",
        "numpy",
        "pypdfium2",
        "jsonschema",
        "aiohttp",
        "python-dotenv",
    ):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
            missing.append(name)
    checks["dependencies"] = {"ok": not missing}
    if missing:
        checks["dependencies"].update(
            missing=missing,
            help="Install dependencies with the project Python: python -m pip install -r requirements.txt",
        )
        result["ok"] = False
        return result
    try:
        import_module("main")

        checks["imports"] = {"ok": True}
    except Exception as error:
        checks["imports"] = {"ok": False, "error_type": type(error).__name__}
        result["ok"] = False
        return result

    from utils.config import get_admin_log_chat_id, get_dean_chat_id, get_tesseract_command, get_token
    from utils.knowledge import get_knowledge

    if not skip_telegram_config or telegram:
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
    if telegram and checks["telegram_configuration"]["ok"]:
        try:
            checks["telegram_api"] = asyncio.run(_telegram_health())
        except Exception as error:
            checks["telegram_api"] = {"ok": False, "error_type": type(error).__name__}
    try:
        knowledge = get_knowledge()
        knowledge.ensure()
        checks["knowledge"] = {"ok": bool(knowledge.get("rule:1.3")), "chunks": len(knowledge.chunks)}
    except Exception as error:
        checks["knowledge"] = {"ok": False, "error_type": type(error).__name__}
    if ocr:
        try:
            import pytesseract
            from PIL import Image, ImageDraw

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
            from utils.local_ai import get_local_ai

            health = get_local_ai().health()
            checks["local_ai"] = {"ok": bool(health.get("available")), **health}
        except Exception as error:
            checks["local_ai"] = {"ok": False, "error_type": type(error).__name__}
    result["ok"] = all(item["ok"] for item in checks.values())
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Read-only local readiness checks. Never sends a Telegram message or document."
    )
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--ai", action="store_true")
    parser.add_argument(
        "--telegram", action="store_true", help="Check getMe and webhook status; never send messages"
    )
    parser.add_argument("--skip-telegram-config", action="store_true")
    args = parser.parse_args()
    readiness = check(
        ocr=args.ocr, ai=args.ai, telegram=args.telegram, skip_telegram_config=args.skip_telegram_config
    )
    print(json.dumps(readiness, ensure_ascii=False, indent=2))
    raise SystemExit(0 if readiness["ok"] else 1)
