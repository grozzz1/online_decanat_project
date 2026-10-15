import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    "utils/ocr.py",
    "utils/documents.py",
    "utils/document_cv.py",
    "utils/document_fields.py",
    "utils/document_classification.py",
    "utils/document_policy.py",
    "utils/document_validation.py",
    "utils/validation.py",
    "utils/hybrid_extraction.py",
    "utils/question_answering.py",
    "utils/knowledge.py",
    "utils/local_ai.py",
    "utils/config.py",
    "rules.md",
    "scripts/benchmark_ai.py",
    "scripts/benchmark_common.py",
    "scripts/benchmark_protocol.py",
)


def fingerprint(inputs, configuration, *, root=ROOT):

    digest = hashlib.sha256()
    for name in SOURCE_FILES:
        digest.update(name.encode("utf-8") + b"\0")
        digest.update((root / name).read_bytes().replace(b"\r\n", b"\n") + b"\0")
    for path in inputs:
        path = Path(path)
        with path.open("rb") as stream:
            content_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        digest.update(path.name.encode("utf-8") + b"\0" + content_hash.encode("ascii"))
    digest.update(json.dumps(configuration, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return digest.hexdigest()


def runtime_settings(client):
    installation = ROOT / "models" / "installation.json"
    runtime = ROOT / ".local_ai" / "runtime.json"
    model = json.loads(installation.read_text(encoding="utf-8")) if installation.exists() else {}
    active = json.loads(runtime.read_text(encoding="utf-8")) if runtime.exists() else {}
    arguments = active.get("arguments", [])[1:]

    flags = [
        value
        for i, value in enumerate(arguments)
        if value != "--model" and (i == 0 or arguments[i - 1] != "--model")
    ]
    return {
        "client": vars(client.config),
        "model": {key: model.get(key) for key in ("engine_release", "model_sha256", "model_revision")},
        "runtime": {"backend": active.get("backend"), "threads": active.get("threads"), "flags": flags},
    }


def revision_metadata():
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        modified = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
        return {"protocol_revision": revision, "source_modified": bool(modified.strip())}
    except (OSError, subprocess.CalledProcessError):
        return {"protocol_revision": None, "source_modified": True}
