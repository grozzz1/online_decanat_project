"""Small local BM25 index with stable clause citations, persisted outside source files."""
from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache
import hashlib
import json
import math
from os import getenv
from pathlib import Path
import re
from threading import RLock

ROOT = Path(__file__).resolve().parents[1]
STOP = set("и в во на по к с со из за о об от до для не ли как какой какая какое какие это или а но что есть указан указана указано укажи значение поля документ документе документу документа положению положения все содержит".split())


def tokens(text):
    # Lightweight Russian stems; no downloaded dictionaries or remote embeddings.
    words = re.findall(r"[а-яёa-z0-9]+", text.casefold().replace("ё", "е"))
    return [word[:6] if len(word) > 6 else word for word in words if word not in STOP and len(word) > 1]


@dataclass(frozen=True)
class Chunk:
    source: str
    text: str
    path: str
    heading: str
    sha256: str
    page: int | None = None


def read_text(path):
    if path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        from utils.documents import pdf_lock
        with pdf_lock, pdfium.PdfDocument(str(path)) as document:
            texts = []
            for page in document:
                with page, page.get_textpage() as textpage:
                    texts.append(textpage.get_text_range())
            return "\n\n".join(texts)
    return path.read_text(encoding="utf-8-sig")


def split_clauses(path, text):
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    heading, current, paragraphs, chunks = "", None, [], []
    def flush():
        if not paragraphs:
            return
        content = "\n".join(paragraphs).strip()
        source = f"rule:{current}" if path.resolve() == (ROOT / "rules.md").resolve() and current else f"kb:{path.stem}:{current or len(chunks)+1}"
        chunks.append(Chunk(source, content, str(path), heading, digest))
    for line in text.replace("\r", "").splitlines():
        match = re.match(r"^\s*(\d+(?:\.\d+)+)\.\s+(.*)", line)
        if line.startswith("#"):
            flush(); paragraphs = []; current = None; heading = line.lstrip("# ")
        elif match:
            flush(); current = match[1]; paragraphs = [line.strip()]
        elif current is not None:
            if line.strip(): paragraphs.append(line.strip())
        elif line.strip():
            paragraphs.append(line.strip())
        elif paragraphs:
            flush(); paragraphs = []
    flush()
    return chunks


class KnowledgeIndex:
    def __init__(self, paths=None, index_path=None):
        self.paths = list(paths or [ROOT / "rules.md"])
        self.index_path = Path(index_path or ROOT / ".local_ai" / "knowledge.json")
        self.lock = RLock()
        self.fingerprint = None
        self.chunks = {}
        self.term_counts = {}
        self.document_frequency = Counter()
        self.average_length = 1
        self.builds = 0

    def _files(self):
        files = []
        for path in self.paths:
            path = Path(path)
            files.extend(sorted(path.rglob("*")) if path.is_dir() else [path])
        return [path for path in files if path.is_file() and path.suffix.lower() in (".md", ".txt", ".pdf")]

    def ensure(self, force=False):
        with self.lock:
            files = self._files()
            stamp = tuple((str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size) for path in files)
            if self.fingerprint == stamp and not force:
                return
            saved = None
            if self.index_path.exists() and not force:
                try:
                    saved = json.loads(self.index_path.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    pass
            if saved and saved.get("fingerprint") == [list(item) for item in stamp]:
                chunks = [Chunk(**item) for item in saved["chunks"]]
            else:
                chunks = [chunk for path in files for chunk in split_clauses(path, read_text(path))]
                self.builds += 1
            self.chunks = {chunk.source: chunk for chunk in chunks}
            if len(self.chunks) != len(chunks):
                raise ValueError("Duplicate knowledge source IDs")
            self.term_counts = {chunk.source: Counter(tokens(chunk.text + " " + chunk.heading)) for chunk in chunks}
            self.document_frequency = Counter(term for counts in self.term_counts.values() for term in counts)
            self.average_length = sum(sum(counts.values()) for counts in self.term_counts.values()) / max(1, len(chunks))
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.index_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"fingerprint": stamp, "chunks": [asdict(chunk) for chunk in chunks]}, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.index_path)
            self.fingerprint = stamp

    def search(self, query, limit=5, *, required_sources=()):
        self.ensure()
        with self.lock:
            terms = set(tokens(query))
            results = []
            for source, counts in self.term_counts.items():
                length = sum(counts.values())
                score = 0
                for term in terms:
                    frequency = counts[term]
                    if frequency:
                        idf = math.log(1 + (len(self.chunks) - self.document_frequency[term] + .5) / (self.document_frequency[term] + .5))
                        score += idf * frequency * 2.5 / (frequency + 1.5 * (.25 + .75 * length / self.average_length))
                if score > 0 or source in required_sources:
                    results.append({**asdict(self.chunks[source]), "score": round(score, 6),
                                    "required_by_validation": source in required_sources})
            results.sort(key=lambda item: (-item["required_by_validation"], -item["score"], item["source"]))
            return results[:max(0, min(limit, 10))]

    def get(self, source):
        self.ensure()
        return self.chunks.get(source)


@lru_cache(maxsize=1)
def get_knowledge():
    extra = [Path(path) for path in getenv("KNOWLEDGE_PATHS", "").split(";") if path.strip()]
    return KnowledgeIndex([ROOT / "rules.md", *extra])
