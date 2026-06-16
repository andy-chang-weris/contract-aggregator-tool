from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Iterable
import unittest


AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from configuration import AGENT_DIR, load_settings
from documents import records_to_documents
from indexing import SearchResult, VectorIndex, make_embedder


class EvaSemanticRetrievalQualityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture_path = AGENT_DIR / "data" / "eva_live_contracts.json"
        records = json.loads(fixture_path.read_text(encoding="utf-8"))
        documents = records_to_documents(records)

        settings = load_settings().with_overrides(
            embedding_provider="sentence-transformers",
            embedding_model="BAAI/bge-small-en-v1.5",
        )
        embedder = make_embedder(settings.embedding_provider, settings.embedding_model)
        cls.index = VectorIndex(embedder)
        cls.index.build(documents)

    def assert_retrieves_any(
        self,
        query: str,
        expected_ids: set[str],
        *,
        top_k: int = 3,
    ) -> None:
        results = self.index.search(query, top_k=top_k)
        retrieved_ids = self._result_external_ids(results)
        self.assertTrue(
            expected_ids.intersection(retrieved_ids),
            msg=(
                f"\nQuery: {query!r}"
                f"\nExpected one of: {sorted(expected_ids)}"
                f"\nRetrieved: {retrieved_ids}"
                f"\nResults:\n{self._format_results(results)}"
            ),
        )

    def assert_top_result(self, query: str, expected_id: str) -> None:
        results = self.index.search(query, top_k=3)
        retrieved_ids = self._result_external_ids(results)
        self.assertTrue(results, msg=f"No results for query: {query!r}")
        self.assertEqual(
            expected_id,
            retrieved_ids[0],
            msg=(
                f"\nQuery: {query!r}"
                f"\nExpected top result: {expected_id}"
                f"\nRetrieved: {retrieved_ids}"
                f"\nResults:\n{self._format_results(results)}"
            ),
        )

    @staticmethod
    def _result_external_ids(results: Iterable[SearchResult]) -> list[str]:
        return [
            str(result.document.metadata.get("external_id") or result.document.doc_id)
            for result in results
        ]

    @staticmethod
    def _format_results(results: Iterable[SearchResult]) -> str:
        lines: list[str] = []
        for result in results:
            external_id = result.document.metadata.get("external_id") or result.document.doc_id
            title = EvaSemanticRetrievalQualityTest._safe_text(
                result.document.metadata.get("title", "")
            )
            lines.append(f"{external_id}: score={result.score:.4f}, title={title}")
        return "\n".join(lines)

    @staticmethod
    def _safe_text(value: object) -> str:
        return str(value).encode("ascii", errors="backslashreplace").decode("ascii")

    def test_specific_queries_rank_expected_document_first(self) -> None:
        cases = [
            (
                "Virginia Tech small project AE services Category B pool contract",
                "170594",
            ),
            (
                "Town of Herndon recreation instruction classes camps Parks and Recreation",
                "18539",
            ),
            (
                "Renovate Norfolk owner occupied single family multi family rental rehab RFQ 7698",
                "101123",
            ),
            (
                "VCCS 2025 2026 small purchase procurement AE services community college campuses",
                "107592",
            ),
            (
                "City of Falls Church RFP 0815-21-CAC camps classes recreational programs",
                "806",
            ),
            (
                "Virginia Trees for Clean Water grant canopy cover water quality",
                "111295",
            ),
            (
                "Real Property For Sale Richmond 1.2 acres Kyle Vernon",
                "112038",
            ),
            (
                "SVP Assisted Living Facility Services sexually violent predators RFI 720-5242",
                "116370",
            ),
            (
                "Radford University Category B Services RFQ CB 26 architectural engineering",
                "116974",
            ),
            (
                "Town of Clifton Streetscape Project duct bank roadway construction Main Street",
                "119188",
            ),
        ]

        for query, expected_id in cases:
            with self.subTest(query=query):
                self.assert_top_result(query, expected_id)

    def test_broad_ae_query_accepts_any_matching_ae_document(self) -> None:
        self.assert_retrieves_any(
            "small project architectural engineering services pool contract",
            {"170594", "107592", "116974"},
            top_k=3,
        )

    def test_broad_camps_query_retrieves_recreation_documents(self) -> None:
        self.assert_retrieves_any(
            "camps classes recreation instruction",
            {"18539", "806"},
            top_k=3,
        )

