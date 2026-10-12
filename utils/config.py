from os import environ, getenv
from pathlib import Path
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def get_token() -> str:
    return environ["BOT_TOKEN"]


def get_dean_chat_id() -> int:
    return int(environ["DEAN_CHAT_ID"])


def get_tesseract_command() -> str:
    local = Path(__file__).resolve().parents[1] / "tools" / "tesseract" / "tesseract.exe"
    return getenv("TESSERACT_CMD") or str(local)


def get_admin_log_chat_id() -> int:
    return int(environ["ADMIN_LOG_CHAT_ID"])
