"""LLM provider abstraction for mock, cloud, and optional local inference."""

from __future__ import annotations

import json
from typing import Any

from configuration import Settings
from generation.prompts import GENERAL_SYSTEM_PROMPT, SYSTEM_PROMPT, build_user_prompt
from indexing import SearchResult


class LLMError(RuntimeError):
    pass


class BaseLLM:
    def answer(self, question: str, results: list[SearchResult]) -> str:
        raise NotImplementedError

    def answer_general(self, question: str) -> str:
        return self.answer(question, [])


class MockLLM(BaseLLM):
    """Deterministic test provider that creates grounded summaries without an external model."""

    def answer(self, question: str, results: list[SearchResult]) -> str:
        if not results:
            return (
                "I did not find a strong contract match in the indexed records. "
                "Try adding keywords for agency, NAICS, location, deadline, technology, or set-aside type."
            )

        lines = [
            "Based on the retrieved contract records, these are the best matches:",
            "",
        ]
        for idx, result in enumerate(results, start=1):
            meta = result.metadata
            title = meta.get("title") or "Untitled contract"
            record_id = meta.get("id") or meta.get("external_id") or "unknown id"
            agency = meta.get("agency") or "agency not listed"
            deadline = meta.get("deadline") or "deadline not listed"
            value = meta.get("contract_value") or "value not listed"
            url = meta.get("url") or "URL not listed"
            reason_bits = []
            text_lower = result.document.text.lower()
            for keyword in ["ai", "cloud", "software", "cyber", "security", "data", "small business"]:
                if keyword in text_lower and keyword in question.lower():
                    reason_bits.append(keyword)
            reason = ", ".join(reason_bits) if reason_bits else "overlap with the query terms and contract metadata"
            lines.extend(
                [
                    f"{idx}. {title} ({record_id})",
                    f"   Agency: {agency}",
                    f"   Deadline: {deadline}; Value: {value}",
                    f"   Why it may fit: {reason}. Retrieval score: {result.score:.3f}.",
                    f"   URL: {url}",
                    "",
                ]
            )
        lines.append("This response is grounded only in the retrieved records. Verify eligibility, deadlines, and requirements in the source listing before acting.")
        return "\n".join(lines)

    def answer_general(self, question: str) -> str:
        normalized = question.strip().lower()
        if normalized in {"hi", "hello", "hey"}:
            return "Hello. I can help answer general questions or search indexed contract opportunities when you ask about contracts."
        return (
            "This agent answers questions about indexed contract opportunities using retrieval when the question is contract-related. "
            "For general questions, it can answer directly without consulting the contract index."
        )


class OpenAICompatibleLLM(BaseLLM):
    """Calls any provider exposing an OpenAI-compatible chat completions endpoint."""

    def __init__(self, settings: Settings, default_base_url: str = "") -> None:
        base_url = settings.llm_base_url or default_base_url
        if not base_url:
            raise LLMError("RAG_LLM_BASE_URL is required for openai-compatible provider.")
        if not settings.llm_api_key:
            raise LLMError("RAG_LLM_API_KEY or OPENAI_API_KEY is required for this LLM provider.")
        self.settings = settings
        self.base_url = base_url.rstrip("/")

    def answer(self, question: str, results: list[SearchResult]) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(question, results)},
            ],
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        import requests

        response = requests.post(url, headers=headers, json=payload, timeout=self.settings.request_timeout)
        if response.status_code >= 400:
            raise LLMError(f"LLM provider returned HTTP {response.status_code}: {response.text[:500]}")
        data = response.json()
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected LLM response shape: {json.dumps(data)[:500]}") from exc

    def answer_general(self, question: str) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": GENERAL_SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        import requests

        response = requests.post(url, headers=headers, json=payload, timeout=self.settings.request_timeout)
        if response.status_code >= 400:
            raise LLMError(f"LLM provider returned HTTP {response.status_code}: {response.text[:500]}")
        data = response.json()
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected LLM response shape: {json.dumps(data)[:500]}") from exc


class BedrockLLM(BaseLLM):
    """Calls an Amazon Bedrock text model through the Converse API."""

    def __init__(self, settings: Settings) -> None:
        if not settings.llm_model:
            raise LLMError("RAG_LLM_MODEL is required for Bedrock provider.")
        self.settings = settings
        self.model_id = settings.llm_model
        self.region = settings.aws_region
        try:
            import boto3  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised only when dependency missing
            raise LLMError("boto3 is required for RAG_LLM_PROVIDER=bedrock.") from exc
        self.client = boto3.client("bedrock-runtime", region_name=self.region)

    def answer(self, question: str, results: list[SearchResult]) -> str:
        return self._converse(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_user_prompt(question, results),
        )

    def answer_general(self, question: str) -> str:
        return self._converse(
            system_prompt=GENERAL_SYSTEM_PROMPT,
            user_prompt=question,
        )

    def _converse(self, system_prompt: str, user_prompt: str) -> str:
        inference_config: dict[str, Any] = {
            "maxTokens": self.settings.max_tokens,
        }
        if self.settings.temperature >= 0:
            inference_config["temperature"] = self.settings.temperature

        try:
            response = self.client.converse(
                modelId=self.model_id,
                system=[{"text": system_prompt}],
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": user_prompt}],
                    }
                ],
                inferenceConfig=inference_config,
            )
        except Exception as exc:
            raise LLMError(f"Bedrock model invocation failed: {exc}") from exc

        content = (
            response.get("output", {})
            .get("message", {})
            .get("content", [])
        )
        text_parts = [
            str(block["text"]).strip()
            for block in content
            if isinstance(block, dict) and block.get("text")
        ]
        answer = "\n".join(part for part in text_parts if part).strip()
        if not answer:
            raise LLMError(f"Unexpected Bedrock response shape: {json.dumps(response, default=str)[:500]}")
        return answer


class OllamaLLM(BaseLLM):
    """Calls a reachable Ollama server. Useful only when Codex/local env has one configured."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.llm_base_url or "http://localhost:11434"

    def answer(self, question: str, results: list[SearchResult]) -> str:
        url = f"{self.base_url}/api/chat"
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(question, results)},
            ],
            "stream": False,
            "options": {
                "temperature": self.settings.temperature,
                "num_predict": self.settings.max_tokens,
            },
        }
        import requests

        response = requests.post(url, json=payload, timeout=self.settings.request_timeout)
        if response.status_code >= 400:
            raise LLMError(f"Ollama returned HTTP {response.status_code}: {response.text[:500]}")
        data = response.json()
        try:
            return str(data["message"]["content"]).strip()
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Unexpected Ollama response shape: {json.dumps(data)[:500]}") from exc

    def answer_general(self, question: str) -> str:
        url = f"{self.base_url}/api/chat"
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": GENERAL_SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            "stream": False,
            "options": {
                "temperature": self.settings.temperature,
                "num_predict": self.settings.max_tokens,
            },
        }
        import requests

        response = requests.post(url, json=payload, timeout=self.settings.request_timeout)
        if response.status_code >= 400:
            raise LLMError(f"Ollama returned HTTP {response.status_code}: {response.text[:500]}")
        data = response.json()
        try:
            return str(data["message"]["content"]).strip()
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Unexpected Ollama response shape: {json.dumps(data)[:500]}") from exc


def make_llm(settings: Settings) -> BaseLLM:
    provider = settings.llm_provider.lower()
    if provider == "mock":
        return MockLLM()
    if provider in {"bedrock", "aws-bedrock", "aws_bedrock"}:
        return BedrockLLM(settings)
    if provider == "openai":
        return OpenAICompatibleLLM(settings, default_base_url="https://api.openai.com/v1")
    if provider in {"openai-compatible", "openai_compatible", "cloud"}:
        return OpenAICompatibleLLM(settings)
    if provider == "ollama":
        return OllamaLLM(settings)
    raise LLMError("RAG_LLM_PROVIDER must be one of: mock, bedrock, openai, openai-compatible, ollama")

