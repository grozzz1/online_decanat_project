import argparse
import getpass
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values, load_dotenv, set_key

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = Path("/etc/online-decanat.env")
NGINX_FILE = Path("/etc/nginx/sites-available/online-decanat")
sys.path.insert(0, str(ROOT))


def validated_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Укажи адрес в формате https://example.org или http://IP без пути.")
    return value.rstrip("/")


def render_nginx(domain="_"):
    return "\n".join(
        [
            "server {",
            "    listen 80 default_server;",
            "    listen [::]:80 default_server;",
            f"    server_name {domain};",
            "    client_max_body_size 11m;",
            "    location / {",
            "        proxy_pass http://127.0.0.1:5000;",
            "        proxy_set_header Host $host;",
            "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
            "        proxy_set_header X-Forwarded-Proto $scheme;",
            "        proxy_set_header X-Forwarded-Host $host;",
            "        proxy_read_timeout 180s;",
            "    }",
            "}",
            "",
        ]
    )


def set_environment(key, value):
    permissions = ENV_FILE.stat()
    set_key(ENV_FILE, key, value)
    if hasattr(os, "chown"):
        os.chown(ENV_FILE, permissions.st_uid, permissions.st_gid)
    os.chmod(ENV_FILE, permissions.st_mode & 0o7777)


def initialize():
    values = {**dotenv_values(ROOT / ".env.example"), **dotenv_values(ENV_FILE)}
    for key, prompt in (
        ("BOT_TOKEN", "Токен бота: "),
        ("DEAN_CHAT_ID", "ID чата деканата: "),
        ("ADMIN_LOG_CHAT_ID", "ID чата журнала: "),
    ):
        value = values.get(key, "") or ""
        if not value or value.startswith("replace_with_") or value in ("-1000000000000", "-1000000000001"):
            if not sys.stdin.isatty():
                raise SystemExit(f"Заполни {key} в {ENV_FILE} и повтори установку.")
            values[key] = getpass.getpass(prompt).strip() if key == "BOT_TOKEN" else input(prompt).strip()
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", values["BOT_TOKEN"]):
        raise SystemExit("Проверь формат BOT_TOKEN в конфигурации.")
    for key in ("DEAN_CHAT_ID", "ADMIN_LOG_CHAT_ID"):
        try:
            int(values[key])
        except (ValueError, TypeError):
            raise SystemExit(f"{key} должен содержать целочисленный ID.") from None
    public = values.get("PUBLIC_BASE_URL") or ""
    if not public or urlsplit(public).hostname in ("localhost", "127.0.0.1", "::1"):
        if not sys.stdin.isatty():
            raise SystemExit(
                f"Заполни PUBLIC_BASE_URL адресом http://IP-сервера или https://домена в {ENV_FILE}."
            )
        public = input("Публичный адрес сервера (http://IP или https://домен): ").strip()
    values["PUBLIC_BASE_URL"] = validated_url(public)
    values.update(
        DATA_DIR="/var/lib/online-decanat",
        ENV_FILE=str(ENV_FILE),
        WEB_HOST="127.0.0.1",
        WEB_PORT="5000",
        TRUST_PROXY="1",
        TESSERACT_CMD=shutil.which("tesseract") or "/usr/bin/tesseract",
    )
    values["FLASK_SECRET_KEY"] = values.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
    for key, value in values.items():
        if value is not None:
            set_environment(key, str(value))
    print(f"Конфигурация сохранена в {ENV_FILE}.")


def enabled():
    return (dotenv_values(ENV_FILE).get("LOCAL_AI_ENABLED") or "1").lower() in ("1", "true", "yes")


def change_url(value):
    set_environment("PUBLIC_BASE_URL", validated_url(value))


def configure_domain(domain, email):
    domain = domain.encode("idna").decode("ascii").lower()
    if len(domain) > 253 or not re.fullmatch(
        r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", domain
    ):
        raise ValueError("Недопустимое доменное имя.")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Укажи email для уведомлений о сертификате.")
    original = NGINX_FILE.read_text(encoding="utf-8")
    updated = re.sub(r"server_name\s+[^;]+;", f"server_name {domain};", original)
    try:
        NGINX_FILE.write_text(updated, encoding="utf-8")
        subprocess.run(["nginx", "-t"], check=True)
        subprocess.run(
            [
                "certbot",
                "--nginx",
                "--non-interactive",
                "--agree-tos",
                "--redirect",
                "--email",
                email,
                "-d",
                domain,
            ],
            check=True,
        )
    except Exception:
        NGINX_FILE.write_text(original, encoding="utf-8")
        subprocess.run(["systemctl", "reload", "nginx"], check=False)
        raise
    change_url("https://" + domain)
    subprocess.run(["systemctl", "reload", "nginx"], check=True)
    print("HTTPS настроен. В BotFather укажи адрес Mini App: https://" + domain + "/miniapp")


def doctor(offline=False):
    load_dotenv(ENV_FILE, override=True)
    os.chdir(ROOT)
    from scripts.doctor import check
    from utils.config import get_data_directory

    report = check(ocr=True, ai=enabled() and not offline, telegram=not offline)
    report["checks"]["data_directory"] = {"ok": os.access(get_data_directory(), os.W_OK)}
    report["ok"] = all(item["ok"] for item in report["checks"].values())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "nginx", "ai-enabled", "doctor", "url", "domain"])
    parser.add_argument("values", nargs="*")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize()
        elif args.command == "nginx":
            if not NGINX_FILE.exists():
                NGINX_FILE.write_text(render_nginx(), encoding="utf-8")
        elif args.command == "ai-enabled":
            print(int(enabled()))
        elif args.command == "doctor":
            return doctor(args.offline)
        elif args.command == "url" and len(args.values) == 1:
            change_url(args.values[0])
        elif args.command == "domain" and len(args.values) == 2:
            configure_domain(*args.values)
        else:
            parser.error("Проверь аргументы команды.")
    except ValueError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
