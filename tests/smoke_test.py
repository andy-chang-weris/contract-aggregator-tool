"""Cloud-safe smoke test for the terminal RAG agent."""

from __future__ import annotations

from uuid import uuid4

from configuration import AGENT_DIR, load_settings
from rag import RagAgent


def main() -> None:
    scratch_root = AGENT_DIR / ".tmp"
    scratch_root.mkdir(exist_ok=True)
    index_path = scratch_root / f"smoke-index-{uuid4().hex}.json"
    try:
        settings = load_settings().with_overrides(
            data_source="sample",
            llm_provider="mock",
            index_path=index_path,
            top_k=3,
            min_score=0.0,
            embedding_provider="hash",
        )
        agent = RagAgent.create(settings, rebuild_index=True)
        response = agent.ask("Find AI cloud cybersecurity software opportunities for a small vendor")
    finally:
        index_path.unlink(missing_ok=True)
    assert response.sources, "Expected at least one retrieved source."
    assert "AI" in response.answer or "Cyber" in response.answer or "software" in response.answer.lower()
    assert response.index_source == "sample"
    print("Smoke test passed: sample data indexed, retrieved, and answered with mock LLM.")


if __name__ == "__main__":
    main()

