"""Retriever wrapper for loading or rebuilding the local vector index."""

from __future__ import annotations

from configuration import Settings
from documents import Document
from indexing import SearchResult, VectorIndex, build_index, make_embedder, tokenize


_GENERIC_CONTRACT_TERMS = {
    "acquisition",
    "agency",
    "award",
    "bid",
    "contract",
    "contracts",
    "deadline",
    "eva",
    "find",
    "government",
    "list",
    "mention",
    "open",
    "opportunity",
    "opportunities",
    "procurement",
    "proposal",
    "quote",
    "record",
    "records",
    "rfb",
    "rfi",
    "rfp",
    "rfq",
    "sam",
    "search",
    "show",
    "solicitation",
    "solicitations",
    "summarize",
    "vendor",
    "virginia",
}

_STOPWORDS = {
    "about",
    "after",
    "all",
    "also",
    "and",
    "any",
    "are",
    "can",
    "does",
    "for",
    "from",
    "has",
    "have",
    "how",
    "into",
    "is",
    "it",
    "its",
    "me",
    "near",
    "that",
    "the",
    "their",
    "there",
    "this",
    "to",
    "using",
    "what",
    "when",
    "where",
    "which",
    "who",
    "with",
}


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
        vector_index = VectorIndex.load(settings.index_path, aws_region=settings.aws_region)
        expected_embedder = make_embedder(settings.embedding_provider, settings.embedding_model, settings.aws_region)
        if vector_index.embedder.provider != expected_embedder.provider or vector_index.embedder.model != expected_embedder.model:
            vector_index, _count, source_label = build_index(settings)
            return cls(settings, vector_index, source_label)
        return cls(settings, vector_index, "existing-index")

    def rebuild(self) -> None:
        self.vector_index, _count, self.source_label = build_index(self.settings)

    def search(self, query: str, top_k: int | None = None) -> list[SearchResult]:
        limit = top_k or self.settings.top_k
        vector_results = self.vector_index.search(query, top_k=max(limit * 4, 20))
        vector_scores = {result.document.doc_id: result.score for result in vector_results}

        query_terms = _specific_query_terms(query)
        if not query_terms:
            return [result for result in vector_results[:limit] if result.score >= self.settings.min_score]

        scored: list[SearchResult] = []
        for entry in self.vector_index.entries:
            document = Document.from_dict(entry["document"])
            coverage_score, lexical_score = _lexical_scores(query_terms, document)
            if lexical_score <= 0:
                continue
            if not _has_enough_query_coverage(query_terms, coverage_score):
                continue
            vector_score = vector_scores.get(document.doc_id, 0.0)
            combined_score = lexical_score + (vector_score * 0.25)
            if combined_score >= self.settings.min_score:
                scored.append(SearchResult(document=document, score=combined_score))

        scored.sort(key=lambda result: result.score, reverse=True)
        return scored[:limit]


def _specific_query_terms(query: str) -> set[str]:
    terms = {_stem(token) for token in tokenize(query)}
    return {
        term
        for term in terms
        if len(term) >= 3 and term not in _GENERIC_CONTRACT_TERMS and term not in _STOPWORDS
    }


def _lexical_scores(query_terms: set[str], document: Document) -> tuple[float, float]:
    doc_terms = {_stem(token) for token in tokenize(document.text)}
    overlap = query_terms.intersection(doc_terms)
    if not overlap:
        return 0.0, 0.0

    coverage_score = len(overlap) / len(query_terms)
    score = coverage_score
    title = str(document.metadata.get("title") or "")
    title_terms = {_stem(token) for token in tokenize(title)}
    title_overlap = query_terms.intersection(title_terms)
    if title_overlap:
        score += 0.25 * (len(title_overlap) / len(query_terms))
    return coverage_score, score


def _has_enough_query_coverage(query_terms: set[str], coverage_score: float) -> bool:
    term_count = len(query_terms)
    if term_count >= 4:
        return coverage_score >= 0.75
    if term_count == 3:
        return coverage_score >= 2 / 3
    return coverage_score > 0


def _stem(token: str) -> str:
    token = token.lower()
    for suffix in (
        "ization",
        "ations",
        "ation",
        "ments",
        "ment",
        "ingly",
        "edly",
        "ing",
        "ies",
        "ied",
        "ed",
        "es",
        "s",
    ):
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            if suffix in {"ies", "ied"}:
                return token[: -len(suffix)] + "y"
            return token[: -len(suffix)]
    return token
