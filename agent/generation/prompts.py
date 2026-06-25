"""Prompts for grounded contract Q&A."""

from __future__ import annotations

from indexing import SearchResult


SYSTEM_PROMPT = """You are a contract opportunity RAG assistant.
Use only the retrieved contract records as evidence.
Never invent contracts, deadlines, values, eligibility details, agencies, or URLs.

When recommending matches, cite each retrieved record with its title, ID, agency, deadline, value, and URL when available.
If a retrieved record partially matches, say what matches and what is missing.
If no retrieved record is relevant, say the evidence is weak and suggest narrower search terms.
Do not claim there are no relevant records while also describing a relevant retrieved record.
""".strip()


GENERAL_SYSTEM_PROMPT = """You are a concise assistant for a contract opportunity RAG tool.
Answer general, non-document questions directly.
Start by saying this is a general model response that does not use retrieved contract records.
Do not mention or cite specific retrieved contract records, because none were provided for this answer.
If asked what the model or agent does, explain that contract-related questions use retrieved indexed contract records, while general questions are answered without retrieval.
""".strip()


def result_to_context(result: SearchResult, number: int) -> str:
    metadata = result.metadata
    fields = [
        ("Title", metadata.get("title")),
        ("ID", metadata.get("id") or metadata.get("external_id")),
        ("Agency", metadata.get("agency")),
        ("Organization", metadata.get("organization")),
        ("Type", metadata.get("contract_type")),
        ("NAICS", metadata.get("naics")),
        ("Place", metadata.get("place_of_performance")),
        ("Posted", metadata.get("posted_date")),
        ("Deadline", metadata.get("deadline")),
        ("Value", metadata.get("contract_value")),
        ("Status", metadata.get("award_status")),
        ("URL", metadata.get("url")),
    ]
    header = f"[Contract {number} | retrieval_score={result.score:.3f}]"
    body = "\n".join(f"{label}: {value}" for label, value in fields if value)
    return f"{header}\n{body}\nText:\n{result.document.text}"


def build_context(results: list[SearchResult]) -> str:
    if not results:
        return "No retrieved contracts."
    return "\n\n---\n\n".join(result_to_context(result, number) for number, result in enumerate(results, start=1))


def build_user_prompt(question: str, results: list[SearchResult]) -> str:
    return f"""User question/preferences:
{question}

Retrieved contract records:
{build_context(results)}

Write a grounded answer using this format:
1. Best matches first. For each match include:
   - Title and ID
   - Agency
   - Deadline and value, or say when missing
   - URL, or say when missing
   - Why it fits the question
2. Uncertainty or missing information.

Only include records from the retrieved contract records above.
""".strip()

