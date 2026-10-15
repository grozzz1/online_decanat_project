"""Check the publishable Git tree without displaying secret values."""

import json
import re
import subprocess
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(
    rb"\b[0-9]{8,12}:[A-Za-z0-9_-]{30,50}\b|\bgh[pousr]_[A-Za-z0-9]{30,}\b|"
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
PRIVATE_DIRECTORIES = {
    ".venv",
    ".tmp",
    ".local_ai",
    "models",
    "benchmark_results",
    "uploads",
    "private_data",
    "logs",
}


def check():
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    names = sorted(set(listing.stdout.decode("utf-8").split("\0")) - {""})
    local = dotenv_values(ROOT / ".env")
    secrets = {
        key: value.encode("utf-8")
        for key, value in local.items()
        if value and len(value) >= 8 and any(word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD"))
    }
    findings = []
    checked = 0
    for name in names:
        path = ROOT / name
        if not path.is_file():  # Deleted entries may still be in the index before staging.
            continue
        checked += 1
        parts = Path(name).parts
        forbidden = (
            parts[0] in PRIVATE_DIRECTORIES
            or name == ".env"
            or (path.name.startswith(".env.") and path.name != ".env.example")
            or path.suffix.lower() in {".gguf", ".exe", ".dll", ".log", ".pem", ".key"}
        )
        if forbidden:
            findings.append({"path": name, "reason": "private_or_generated_file"})
        if path.stat().st_size > 5 * 1024 * 1024:
            findings.append({"path": name, "reason": "file_over_5_MiB"})
            continue
        content = path.read_bytes()
        if TOKEN.search(content):
            findings.append({"path": name, "reason": "credential_pattern"})
        for key, value in secrets.items():
            if value in content:
                findings.append({"path": name, "reason": "local_secret_value", "variable": key})
    return {
        "ok": not findings,
        "checked_files": checked,
        "findings": findings,
        "scope": "working files publishable by Git; history was audited separately",
    }


if __name__ == "__main__":
    report = check()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ok"] else 1)
