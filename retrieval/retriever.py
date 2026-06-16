"""Retriever wrapper for loading or rebuilding the local vector index."""

from __future__ import annotations

from configuration import Settings
from indexing import SearchResult, VectorIndex, build_index


class ContractRetriever:
    def __init__(self, settings: Settings, vector_index: VectorIndex, source_label: str) -> None:
        self.settings = settings
        self.vector_index = vector_index
        self.source_label = source_label

    @classmethod
    def create(cls, settings: Settings, rebuild: bool = False) -> "ContractRetriever":
        if rebuild or not settings.index_path.exists():
            vector_index, _count, source_label = build_index(settings)
            return cls(settings, vector_index, source_label)
        return cls(settings, VectorIndex.load(settings.index_path), "existing-index")

    def rebuild(self) -> None:
        self.vector_index, _count, self.source_label = build_index(self.settings)

    def search(self, query: str, top_k: int | None = None) -> list[SearchResult]:
        results = self.vector_index.search(query, top_k=top_k or self.settings.top_k)
        return [result for result in results if result.score >= self.settings.min_score]

