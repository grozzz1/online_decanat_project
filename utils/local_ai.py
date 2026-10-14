"""One shared, bounded client for a loopback-only llama.cpp/Ollama server."""
from collections import Counter, deque
from dataclasses import dataclass
from functools import lru_cache
import http.client
import json
import logging
from os import getenv
from threading import BoundedSemaphore, Lock
from time import monotonic, perf_counter
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from utils import config  # Load the project's .env without logging it.

logger = logging.getLogger("decanat.ai")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s AI %(message)s"))
    logger.addHandler(handler)
logger.propagate = False


@dataclass(frozen=True)
class AIConfig:
    enabled: bool = True
    endpoint: str = "http://127.0.0.1:18081"
    engine: str = "llamacpp"
    model: str = "qwen3-4b-instruct"
    timeout: float = 90
    queue_timeout: float = 5
    max_tokens: int = 512
    context_chars: int = 12000
    cooldown: float = 30

    @classmethod
    def from_env(cls):
        return cls(enabled=getenv("LOCAL_AI_ENABLED", "1").lower() in ("1", "true", "yes"),
                   endpoint=getenv("LOCAL_AI_URL", cls.endpoint), engine=getenv("LOCAL_AI_ENGINE", cls.engine),
                   model=getenv("LOCAL_AI_MODEL", cls.model), timeout=float(getenv("LOCAL_AI_TIMEOUT", "90")),
                   queue_timeout=float(getenv("LOCAL_AI_QUEUE_TIMEOUT", "5")),
                   max_tokens=int(getenv("LOCAL_AI_MAX_TOKENS", "512")),
                   context_chars=int(getenv("LOCAL_AI_CONTEXT_CHARS", "12000")),
                   cooldown=float(getenv("LOCAL_AI_COOLDOWN", "30")))


class AIError(RuntimeError):
    """Safe diagnostic code only; never contains the document or response body."""


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AIError("duplicate_json_key")
            result[key] = value
        return result
    try:
        return json.loads(text, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(AIError("nonfinite_json")))
    except (ValueError, TypeError, RecursionError) as error:
        raise AIError("invalid_json") from error


class LocalAI:
    def __init__(self, settings=None):
        self.config = settings or AIConfig.from_env()
        url = urlsplit(self.config.endpoint)
        if url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1") or url.username or url.query or url.path not in ("", "/"):
            raise ValueError("LOCAL_AI_URL must be an HTTP loopback origin")
        if self.config.engine not in ("llamacpp", "ollama"):
            raise ValueError("Unsupported LOCAL_AI_ENGINE")
        self.host, self.port = url.hostname, url.port or 80
        self.gate = BoundedSemaphore(1)
        self.lock = Lock()
        self.unavailable_until = 0
        self.records = deque(maxlen=5000)
        self.counters = Counter()

    def _request(self, path, payload=None, timeout=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout or self.config.timeout)
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
            connection.request("POST" if body else "GET", path, body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise AIError(f"http_{response.status}")
            data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise AIError("response_too_large")
            return strict_json(data)
        finally:
            connection.close()

    def health(self):
        try:
            result = self._request("/api/tags" if self.config.engine == "ollama" else "/health", timeout=3)
            return {"available": True, "model": self.config.model, "engine": self.config.engine, "server": result}
        except (OSError, AIError, http.client.HTTPException):
            return {"available": False, "model": self.config.model, "engine": self.config.engine}

    def generate(self, system, payload, schema, *, purpose="qa", max_tokens=None):
        if not self.config.enabled:
            raise AIError("disabled")
        if monotonic() < self.unavailable_until:
            raise AIError("circuit_open")
        if not self.gate.acquire(timeout=self.config.queue_timeout):
            raise AIError("queue_timeout")
        started = perf_counter()
        record = {"purpose": purpose, "model": self.config.model, "engine": self.config.engine}
        try:
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False) + "\n/no_think"}]
            tokens = min(max_tokens or self.config.max_tokens, 2048)
            if self.config.engine == "ollama":
                data = self._request("/api/chat", {"model": self.config.model, "messages": messages,
                    "stream": False, "think": False, "format": schema, "keep_alive": "-1m",
                    "options": {"temperature": 0, "num_predict": tokens, "num_ctx": 4096, "seed": 42}})
                content = data["message"]["content"]
                record.update(prompt_tokens=data.get("prompt_eval_count"), completion_tokens=data.get("eval_count"))
            else:
                data = self._request("/v1/chat/completions", {"model": self.config.model, "messages": messages,
                    "stream": False, "temperature": 0, "seed": 42, "max_tokens": tokens,
                    "chat_template_kwargs": {"enable_thinking": False},
                    "response_format": {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}}})
                content = data["choices"][0]["message"]["content"]
                record.update(data.get("usage", {}))
                if data["choices"][0].get("finish_reason") == "length":
                    raise AIError("generation_truncated")
            result = strict_json(content)
            if not Draft202012Validator(schema).is_valid(result):
                raise AIError("json_schema")
            record["status"] = "success"
            return result
        except (OSError, http.client.HTTPException) as error:
            self.unavailable_until = monotonic() + self.config.cooldown
            record["status"] = type(error).__name__
            raise AIError("transport_unavailable") from error
        except (KeyError, TypeError, IndexError) as error:
            record["status"] = "invalid_envelope"
            raise AIError("invalid_envelope") from error
        except AIError as error:
            record["status"] = str(error)
            if str(error).startswith("http_"):
                self.unavailable_until = monotonic() + self.config.cooldown
            raise
        finally:
            record["seconds"] = round(perf_counter() - started, 6)
            with self.lock:
                self.records.append(record)
                self.counters[record.get("status", "error")] += 1
            logger.info("inference purpose=%s status=%s seconds=%.3f", purpose, record.get("status"), record["seconds"])
            self.gate.release()


@lru_cache(maxsize=1)
def get_local_ai():
    return LocalAI()
