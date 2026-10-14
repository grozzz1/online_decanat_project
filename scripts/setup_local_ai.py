"""Install pinned, portable local inference artifacts; no document data leaves the PC."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "b11429"
REPO = "unsloth/Qwen3-4B-Instruct-2507-GGUF"
REVISION = "a06e946bb6b655725eafa393f4a9745d460374c9"
MODEL = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"


def download(url, target, expected_sha=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    if not target.exists():
        offset = partial.stat().st_size if partial.exists() else 0
        request = Request(url, headers={"Range": f"bytes={offset}-"} if offset else {})
        with urlopen(request, timeout=90) as response:
            resume = offset and response.status == 206
            with partial.open("ab" if resume else "wb") as output:
                while chunk := response.read(4 * 1024 * 1024):
                    output.write(chunk)
        partial.replace(target)
    digest = hashlib.file_digest(target.open("rb"), "sha256").hexdigest()
    if expected_sha and digest != expected_sha:
        raise RuntimeError(f"SHA256 mismatch: {target.name}; remove the corrupt artifact and retry")
    return digest


def setup(gpu=False):
    backend = "vulkan" if gpu else "cpu"
    archive = ROOT / ".tmp" / f"llama-{RELEASE}-{backend}.zip"
    url = f"https://github.com/ggml-org/llama.cpp/releases/download/{RELEASE}/llama-{RELEASE}-bin-win-{backend}-x64.zip"
    engine_sha = download(url, archive)
    destination = ROOT / "tools" / "llama.cpp" / backend
    destination.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive) as zipped:
        for member in zipped.infolist():
            if not (destination / member.filename).resolve().is_relative_to(destination.resolve()):
                raise RuntimeError("Unsafe archive path")
        zipped.extractall(destination)
    api = f"https://huggingface.co/api/models/{REPO}/tree/{REVISION}?recursive=true&expand=true"
    with urlopen(api, timeout=30) as response:
        entries = json.load(response)
    entry = next(item for item in entries if item["path"] == MODEL)
    expected = entry["lfs"]["oid"]
    print(f"Downloading {REPO}@{REVISION}: {MODEL}, {entry['size']/1e9:.2f} GB", flush=True)
    model_sha = download(f"https://huggingface.co/{REPO}/resolve/{REVISION}/{MODEL}", ROOT / "models" / MODEL, expected)
    metadata = {"engine_release": RELEASE, "engine_backend": backend, "engine_archive_sha256": engine_sha,
                "model_repository": REPO, "model_revision": REVISION, "model_file": MODEL,
                "model_sha256": model_sha, "quantization": "Q4_K_M", "thinking": False}
    (ROOT / "models" / "installation.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print("Installed. Start: python scripts/start_local_ai.py", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", action="store_true", help="Install Vulkan instead of CPU build")
    setup(parser.parse_args().gpu)
