from __future__ import annotations

from pathlib import Path
import sys
import unittest


AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from configuration import load_settings
from documents import Document
from indexing import SearchResult
from rag import RagAgent


class FakeRetriever:
    source_label = "fake-contract-index"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.result = SearchResult(
            document=Document(
                doc_id="contract-123",
                text="Title: Cloud Migration Support\nAgency: Example Agency",
                metadata={
                    "id": "contract-123",
                    "title": "Cloud Migration Support",
                    "agency": "Example Agency",
                    "deadline": "2026-07-01",
                    "contract_value": "$1M",
                    "url": "https://example.invalid/contract-123",
                },
            ),
            score=0.97,
        )

    def search(self, query: str) -> list[SearchResult]:
        self.queries.append(query)
        return [self.result]


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[SearchResult]]] = []
        self.general_calls: list[str] = []

    def answer(self, question: str, results: list[SearchResult]) -> str:
        self.calls.append((question, results))
        if results:
            return "Based on retrieved contract records, this is a RAG answer."
        return "This is a general model response, not using retrieved contract records."

    def answer_general(self, question: str) -> str:
        self.general_calls.append(question)
        return "This is a general model response, not using retrieved contract records."


class GeneralFallbackTest(unittest.TestCase):
    def test_general_queries_bypass_retrieval_sources_and_citations(self) -> None:
        general_queries = ["hi", "what does this model do?"]

        for query in general_queries:
            with self.subTest(query=query):
                retriever = FakeRetriever()
                llm = FakeLLM()
                agent = RagAgent(load_settings(), retriever, llm)

                response = agent.ask(query)

                self.assertEqual(retriever.queries, [])
                self.assertEqual(llm.calls, [])
                self.assertEqual(llm.general_calls, [query])
                self.assertEqual(response.sources, [])
                self.assertIn("general model response", response.answer.lower())
                self.assertIn("not using retrieved contract records", response.answer.lower())
                self.assertNotIn("Retrieved sources:", response.answer)
                self.assertNotIn("Cloud Migration Support", response.answer)
                self.assertNotIn("contract-123", response.answer)

    def test_contract_queries_still_use_rag_even_with_general_words(self) -> None:
        rag_queries = [
            "help me find cloud contracts",
            "what is the deadline for Cloud Migration Support?",
        ]

        for query in rag_queries:
            with self.subTest(query=query):
                retriever = FakeRetriever()
                llm = FakeLLM()
                agent = RagAgent(load_settings(), retriever, llm)

                response = agent.ask(query)

                self.assertEqual(retriever.queries, [query])
                self.assertEqual(llm.general_calls, [])
                self.assertEqual(len(llm.calls), 1)
                self.assertEqual(response.sources, [retriever.result])
                self.assertIn("Retrieved sources:", response.answer)
                self.assertIn("Cloud Migration Support", response.answer)
                self.assertIn("contract-123", response.answer)


if __name__ == "__main__":
    unittest.main()

