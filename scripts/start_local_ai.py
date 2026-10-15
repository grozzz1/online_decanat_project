"""Run one portable llama.cpp server on loopback; Ctrl+C stops it."""

import argparse
import json
import subprocess
import sys
from os import getenv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.setup_local_ai import MODEL
from utils.local_ai import get_local_ai


def start(gpu=False, threads=4):
    client = get_local_ai()
    if client.health()["available"]:
        print("A local AI server is already listening; no second model copy started.")
        return 0
    engines = list((ROOT / "tools" / "llama.cpp" / ("vulkan" if gpu else "cpu")).rglob("llama-server.exe"))
    model = Path(getenv("LOCAL_AI_MODEL_PATH") or str(ROOT / "models" / MODEL))
    if not engines or not model.exists():
        raise SystemExit("Run python scripts/setup_local_ai.py first (with --gpu for Vulkan).")
    arguments = [
        str(engines[0]),
        "--model",
        str(model),
        "--alias",
        client.config.model,
        "--host",
        client.host,
        "--port",
        str(client.port),
        "--ctx-size",
        "4096",
        "--threads",
        str(threads),
        "--parallel",
        "1",
        "--batch-size",
        "256",
        "--ubatch-size",
        "64",
        "--n-gpu-layers",
        "auto" if gpu else "0",
        "--jinja",
        "--reasoning",
        "off",
        "--no-ui",
    ]
    runtime = ROOT / ".local_ai" / "runtime.json"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(
        json.dumps(
            {
                "backend": "vulkan" if gpu else "cpu",
                "threads": threads,
                "engine": str(engines[0]),
                "arguments": arguments,
                "model_file": str(model),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        return subprocess.call(arguments, cwd=ROOT)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    raise SystemExit(start(args.gpu, args.threads))
