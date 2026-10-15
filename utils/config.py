from os import environ, getenv
from pathlib import Path
from shutil import which

from dotenv import load_dotenv

load_dotenv(Path(getenv("ENV_FILE") or Path(__file__).resolve().parents[1] / ".env"))


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


def get_data_directory() -> Path:
    return (
        Path(getenv("DATA_DIR") or Path(__file__).resolve().parents[1] / "private_data")
        .expanduser()
        .resolve()
    )


def get_public_base_url() -> str:
    from urllib.parse import urlsplit

    value = getenv("PUBLIC_BASE_URL", "http://localhost:5000").rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.query
        or parsed.fragment
        or parsed.path
    ):
        raise ValueError("PUBLIC_BASE_URL должен содержать только http(s)://домен[:порт].")
    return value


def get_web_binding():
    host = getenv("WEB_HOST", "127.0.0.1")
    try:
        port = int(getenv("WEB_PORT", "5000"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError("WEB_PORT должен быть целым числом от 1 до 65535.") from None
    return host, port


def get_ai_state_directory() -> Path:
    return (
        get_data_directory() / "local_ai"
        if getenv("DATA_DIR")
        else Path(__file__).resolve().parents[1] / ".local_ai"
    )
