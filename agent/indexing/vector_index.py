"""Build and query a small persistent vector index.

The default embedder is a deterministic hash-vector embedder that needs no model,
GPU, API key, or network. A sentence-transformers embedder can be enabled with
RAG_EMBEDDING_PROVIDER=sentence-transformers when the package and model are
available in the environment.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Protocol

from configuration import Settings, load_settings
from data.sources import load_contract_records
from documents import Document, records_to_documents


TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./+-]{1,}")


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


class Embedder(Protocol):
    provider: str
    model: str

    def embed(self, text: str) -> dict[str, float]:
        ...


class HashVectorEmbedder:
    """Tiny sparse hashing embedder for cloud-safe retrieval smoke tests."""

    def __init__(self, dimensions: int = 768) -> None:
        self.dimensions = dimensions
        self.provider = "hash"
        self.model = f"hash-vector-{dimensions}"

    def embed(self, text: str) -> dict[str, float]:
        tokens = tokenize(text)
        features: list[str] = tokens[:]
        features.extend(f"{left}_{right}" for left, right in zip(tokens, tokens[1:]))
        counts: Counter[int] = Counter()
        for feature in features:
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1 if digest[4] % 2 == 0 else -1
            counts[bucket] += sign

        norm = math.sqrt(sum(value * value for value in counts.values())) or 1.0
        return {str(key): value / norm for key, value in counts.items() if value}


class SentenceTransformerEmbedder:
    """Optional CPU-compatible semantic embedder.

    This is intentionally not the default because first use may require a model
    download. Use RAG_EMBEDDING_PROVIDER=sentence-transformers in environments
    where dependencies and network/model cache are available.
    """

    def __init__(self, model_name: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError(
                "sentence-transformers is required for RAG_EMBEDDING_PROVIDER=sentence-transformers. "
                "Install optional embedding dependencies or use RAG_EMBEDDING_PROVIDER=hash."
            ) from exc

        self.provider = "sentence-transformers"
        self.model = model_name
        self._model = SentenceTransformer(_cached_huggingface_model_path(model_name) or model_name)

    def embed(self, text: str) -> dict[str, float]:
        vector = self._model.encode(text, normalize_embeddings=True)
        return {str(index): float(value) for index, value in enumerate(vector) if float(value)}


def make_embedder(provider: str, model: str) -> Embedder:
    normalized = provider.strip().lower()
    if normalized in {"hash", "hash-vector", "mock"}:
        return HashVectorEmbedder()
    if normalized in {"sentence-transformers", "sentence_transformers", "st"}:
        return SentenceTransformerEmbedder(model)
    raise RuntimeError("RAG_EMBEDDING_PROVIDER must be one of: hash, sentence-transformers")


def _cached_huggingface_model_path(model_name: str) -> str | None:
    """Return a cached snapshot path so local model loads do not need network metadata."""
    try:
        from huggingface_hub import scan_cache_dir  # type: ignore
    except Exception:
        return None

    try:
        cache_info = scan_cache_dir()
    except Exception:
        return None

    matching_repos = [repo for repo in cache_info.repos if repo.repo_id == model_name]
    if not matching_repos:
        return None

    revisions = [
        revision
        for repo in matching_repos
        for revision in repo.revisions
        if revision.snapshot_path.exists()
    ]
    if not revisions:
        return None

    newest_revision = max(revisions, key=lambda revision: revision.last_modified or 0)
    return str(newest_revision.snapshot_path)


@dataclass(frozen=True)
class SearchResult:
    document: Document
    score: float

    @property
    def metadata(self) -> dict[str, Any]:
        return self.document.metadata


class VectorIndex:
    def __init__(self, embedder: Embedder | None = None) -> None:
        self.embedder = embedder or HashVectorEmbedder()
        self.entries: list[dict[str, Any]] = []

    def build(self, documents: list[Document]) -> None:
        self.entries = [
            {"document": document.to_dict(), "embedding": self.embedder.embed(document.text)}
            for document in documents
        ]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "embedding_provider": self.embedder.provider,
            "embedding_model": self.embedder.model,
            "entries": self.entries,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "VectorIndex":
        payload = json.loads(path.read_text(encoding="utf-8"))
        embedder = make_embedder(
            str(payload.get("embedding_provider") or "hash"),
            str(payload.get("embedding_model") or "BAAI/bge-small-en-v1.5"),
        )
        index = cls(embedder=embedder)
        index.entries = list(payload.get("entries", []))
        return index

    def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        query_embedding = self.embedder.embed(query)
        scored: list[SearchResult] = []
        for entry in self.entries:
            score = _dot(query_embedding, entry.get("embedding", {}))
            if score <= 0:
                continue
            document = Document.from_dict(entry["document"])
            scored.append(SearchResult(document=document, score=score))
        scored.sort(key=lambda result: result.score, reverse=True)
        return scored[:top_k]


def _dot(left: dict[str, float], right: dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * float(right.get(key, 0.0)) for key, value in left.items())


def build_index(settings: Settings) -> tuple[VectorIndex, int, str]:
    records, source_label = load_contract_records(settings)
    documents = records_to_documents(records)
    if not documents:
        raise RuntimeError(f"No indexable contract documents loaded from {source_label}.")
    vector_index = VectorIndex(make_embedder(settings.embedding_provider, settings.embedding_model))
    vector_index.build(documents)
    vector_index.save(settings.index_path)
    return vector_index, len(documents), source_label


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the terminal RAG contract index.")
    parser.add_argument("--source", choices=["auto", "db", "dump", "sample"], help="Contract data source.")
    parser.add_argument("--index-path", help="Where to write the JSON vector index.")
    parser.add_argument("--rebuild", action="store_true", help="Accepted for command symmetry; index.py always builds.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings().with_overrides(
        data_source=args.source,
        index_path=Path(args.index_path).expanduser() if args.index_path else None,
    )
    try:
        index, count, source_label = build_index(settings)
    except Exception as exc:
        print(f"Unable to build the RAG index: {exc}", file=sys.stderr)
        if settings.data_source == "dump":
            print("Try installing PostgreSQL client tools for pg_restore, or export a plain COPY-format SQL dump.", file=sys.stderr)
        elif settings.data_source == "db":
            print("Check DB_* environment variables, or run with --source sample for the offline smoke path.", file=sys.stderr)
        return 1
    _ = index
    print(f"Indexed {count} contract documents from {source_label} into {settings.index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

