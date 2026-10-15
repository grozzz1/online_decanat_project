import argparse
import hashlib
import json
import platform
import tarfile
from http.client import HTTPException
from pathlib import Path
from time import sleep
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "b11429"
REPO = "unsloth/Qwen3-4B-Instruct-2507-GGUF"
REVISION = "a06e946bb6b655725eafa393f4a9745d460374c9"
MODEL = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
MODEL_SHA256 = "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597"
MODEL_SIZE = 2497281120


def _transfer(url, partial):
    offset = partial.stat().st_size if partial.exists() else 0
    request = Request(url, headers={"Range": f"bytes={offset}-"} if offset else {})
    with urlopen(request, timeout=90) as response:
        resume = offset and response.status == 206
        if resume and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
            raise OSError("Server returned an unexpected download range")
        length = response.headers.get("Content-Length")
        expected = int(length) if length is not None else None
        received = 0
        progress = 0
        with partial.open("ab" if resume else "wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                received += len(chunk)
                if received - progress >= 64 * 1024 * 1024:
                    progress = received
                    completed = received + (offset if resume else 0)
                    print(f"{partial.name}: {completed / 1024**2:.0f} MiB downloaded", flush=True)
        if expected is not None and received != expected:
            raise OSError("Download ended before the complete file was received")


def download(url, target, expected_sha=None, *, attempts=8):
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    if not target.exists():
        for attempt in range(1, attempts + 1):
            try:
                _transfer(url, partial)
                break
            except (OSError, HTTPException) as error:
                if isinstance(error, HTTPError) and error.code not in (408, 429, 500, 502, 503, 504):
                    raise
                if isinstance(error, PermissionError):
                    raise
                if attempt == attempts:
                    raise RuntimeError(f"Download failed: {target.name}; rerun setup to resume") from error
                print(
                    f"{target.name}: {type(error).__name__}; retry {attempt}/{attempts - 1}, keeping downloaded data",
                    flush=True,
                )
                sleep(min(2 * attempt, 10))
    artifact = target if target.exists() else partial
    with artifact.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if expected_sha and digest != expected_sha:
        raise RuntimeError(f"SHA256 mismatch: {artifact.name}; remove the corrupt artifact and retry")
    if artifact == partial:
        partial.replace(target)
    return digest


def engine_artifact(gpu=False):
    system = platform.system()
    if platform.machine().lower() not in ("amd64", "x86_64"):
        raise RuntimeError("Automatic local AI setup requires x64 Windows or Linux")
    if system == "Windows":
        backend = "vulkan" if gpu else "cpu"
        digest = None if gpu else "1283323272b04cd07905816a597a0da810918102de958f4ff6f7bbaa70ed2efe"
        return f"llama-{RELEASE}-bin-win-{backend}-x64.zip", digest
    if system == "Linux":
        digest = (
            "632c4e98feba2b94407a2130e3133e0c3aefb0ea1ab41337e926d8bfafdd0b74"
            if gpu
            else "f6d25dde8f51133143d1453da4fd5f73b145127177612a283bf7995957af3392"
        )
        return f"llama-{RELEASE}-bin-ubuntu-{'vulkan-' if gpu else ''}x64.tar.gz", digest
    raise RuntimeError("Automatic local AI setup supports Windows and Linux x64")


def setup(gpu=False):
    backend = "vulkan" if gpu else "cpu"
    artifact, digest = engine_artifact(gpu)
    extension = ".zip" if artifact.endswith(".zip") else ".tar.gz"
    archive = ROOT / ".tmp" / f"llama-{RELEASE}-{backend}{extension}"
    url = f"https://github.com/ggml-org/llama.cpp/releases/download/{RELEASE}/{artifact}"
    engine_sha = download(url, archive, digest)
    destination = ROOT / "tools" / "llama.cpp" / backend
    destination.mkdir(parents=True, exist_ok=True)
    if extension == ".zip":
        with ZipFile(archive) as zipped:
            for member in zipped.infolist():
                if not (destination / member.filename).resolve().is_relative_to(destination.resolve()):
                    raise RuntimeError("Unsafe archive path")
            zipped.extractall(destination)
    else:
        with tarfile.open(archive) as packed:
            packed.extractall(destination, filter="data")
    print(f"Preparing {REPO}@{REVISION}: {MODEL}, {MODEL_SIZE / 1e9:.2f} GB", flush=True)
    model_sha = download(
        f"https://huggingface.co/{REPO}/resolve/{REVISION}/{MODEL}", ROOT / "models" / MODEL, MODEL_SHA256
    )
    metadata = {
        "engine_release": RELEASE,
        "engine_backend": backend,
        "engine_archive_sha256": engine_sha,
        "model_repository": REPO,
        "model_revision": REVISION,
        "model_file": MODEL,
        "model_sha256": model_sha,
        "quantization": "Q4_K_M",
        "thinking": False,
    }
    (ROOT / "models" / "installation.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print("Installed. Start: python scripts/start_local_ai.py", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", action="store_true", help="Install Vulkan instead of CPU build")
    setup(parser.parse_args().gpu)
