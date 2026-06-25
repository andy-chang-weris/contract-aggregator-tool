"""High-level orchestration for retrieval plus grounded answering."""

from __future__ import annotations

from dataclasses import dataclass
import re

from configuration import Settings, load_settings
from generation import BaseLLM, make_llm
from indexing import SearchResult
from retrieval import ContractRetriever


@dataclass(frozen=True)
class AgentResponse:
    answer: str
    sources: list[SearchResult]
    index_source: str


class RagAgent:
    def __init__(self, settings: Settings, retriever: ContractRetriever, llm: BaseLLM) -> None:
        self.settings = settings
        self.retriever = retriever
        self.llm = llm
        self.last_response: AgentResponse | None = None

    @classmethod
    def create(cls, settings: Settings | None = None, rebuild_index: bool = False) -> "RagAgent":
        resolved_settings = settings or load_settings()
        retriever = ContractRetriever.create(resolved_settings, rebuild=rebuild_index)
        llm = make_llm(resolved_settings)
        return cls(resolved_settings, retriever, llm)

    def ask(self, question: str) -> AgentResponse:
        if _should_answer_without_rag(question):
            answer = self.llm.answer_general(question)
            answer = _append_general_fallback_disclosure(answer)
            response = AgentResponse(answer=answer, sources=[], index_source="general-model")
            self.last_response = response
            return response

        results = self.retriever.search(question)
        answer = self.llm.answer(question, results)
        answer = _append_source_citations(answer, results)
        response = AgentResponse(answer=answer, sources=results, index_source=self.retriever.source_label)
        self.last_response = response
        return response

    def rebuild_index(self) -> None:
        self.retriever.rebuild()


def _append_source_citations(answer: str, results: list[SearchResult]) -> str:
    if not results:
        return answer

    lines = ["", "Retrieved sources:"]
    for index, result in enumerate(results, start=1):
        meta = result.metadata
        title = meta.get("title") or "Untitled contract"
        record_id = meta.get("id") or meta.get("external_id") or "unknown id"
        agency = meta.get("agency") or "agency not listed"
        deadline = meta.get("deadline") or "deadline not listed"
        value = meta.get("contract_value") or "value not listed"
        url = meta.get("url") or "URL not listed"
        lines.append(
            f"{index}. {title} ({record_id}) | agency={agency} | "
            f"deadline={deadline} | value={value} | url={url} | score={result.score:.3f}"
        )
    return answer.rstrip() + "\n" + "\n".join(lines)


def _append_general_fallback_disclosure(answer: str) -> str:
    disclosure = "Retrieved contract records were not used for this answer."
    stripped = answer.strip()
    if disclosure.lower() in stripped.lower():
        return stripped
    if not stripped:
        return disclosure
    return f"{stripped}\n\n{disclosure}"


_GENERAL_GREETINGS = {
    "good afternoon",
    "good evening",
    "good morning",
    "hello",
    "hey",
    "hi",
    "yo",
}

_GENERAL_HELP_QUERIES = {
    "help",
    "how do you work",
    "what can you do",
    "what do you do",
    "what does this agent do",
    "what does this model do",
    "what does this tool do",
    "what is this",
    "what is this agent",
    "what is this model",
    "what is this tool",
    "who are you",
}


def _normalize_general_query(question: str) -> str:
    normalized = re.sub(r"[^a-z0-9\s]", " ", question.lower())
    return " ".join(normalized.split())


def _should_answer_without_rag(question: str) -> bool:
    normalized = _normalize_general_query(question)
    if not normalized:
        return True
    if normalized in _GENERAL_GREETINGS or normalized in _GENERAL_HELP_QUERIES:
        return True
    return bool(re.fullmatch(r"(what|who) (are|is) (you|this assistant|this model)", normalized))

