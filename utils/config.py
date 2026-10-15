from os import environ, getenv
from pathlib import Path
from shutil import which

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def _required(name: str) -> str:
    value = environ.get(name, "").strip()
    if not value or value.startswith("replace_with_"):
        raise ValueError(f"Настрой {name} в .env (пример: .env.example).")
    return value


def _chat_id(name: str) -> int:
    try:
        return int(_required(name))
    except ValueError:
        raise ValueError(f"{name} в .env должен содержать целочисленный ID чата.") from None


def get_token() -> str:
    return _required("BOT_TOKEN")


def get_dean_chat_id() -> int:
    return _chat_id("DEAN_CHAT_ID")


def get_tesseract_command() -> str:
    local = Path(__file__).resolve().parents[1] / "tools" / "tesseract" / "tesseract.exe"
    return getenv("TESSERACT_CMD") or (str(local) if local.is_file() else which("tesseract")) or str(local)


def get_admin_log_chat_id() -> int:
    return _chat_id("ADMIN_LOG_CHAT_ID")
