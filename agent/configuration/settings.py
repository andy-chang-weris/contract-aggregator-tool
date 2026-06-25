"""Configuration for the standalone terminal RAG agent."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
from typing import Any


AGENT_DIR = Path(__file__).resolve().parents[1]


def _load_env_file() -> None:
    """Load .env when python-dotenv is installed; otherwise use a tiny fallback parser."""
    env_path = AGENT_DIR / ".env"
    if not env_path.exists():
        return

    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(env_path)
        return
    except Exception:
        pass

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None else value


def _int_env(name: str, default: int) -> int:
    raw = _env(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = _env(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _path_env(name: str, default: Path) -> Path:
    raw = _env(name, str(default)).strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = AGENT_DIR / path
    return path


@dataclass(frozen=True)
class Settings:
    data_source: str
    index_path: Path
    sample_data_path: Path
    dump_path: Path
    top_k: int
    min_score: float
    db_limit: int

    embedding_provider: str
    embedding_model: str

    db_host: str
    db_port: str
    db_name: str
    db_user: str
    db_password: str

    llm_provider: str
    llm_model: str
    llm_base_url: str
    llm_api_key: str
    temperature: float
    max_tokens: int
    request_timeout: int

    def with_overrides(self, **kwargs: Any) -> "Settings":
        return replace(self, **{key: value for key, value in kwargs.items() if value is not None})



def load_settings() -> Settings:
    _load_env_file()
    return Settings(
        data_source=_env("RAG_DATA_SOURCE", "auto").strip().lower(),
        index_path=_path_env("RAG_INDEX_PATH", AGENT_DIR / ".rag_index" / "index.json"),
        sample_data_path=_path_env("RAG_SAMPLE_DATA_PATH", AGENT_DIR / "data" / "sample_contracts.json"),
        dump_path=_path_env("RAG_DUMP_PATH", AGENT_DIR / "contracts.sql"),
        top_k=_int_env("RAG_TOP_K", 5),
        min_score=_float_env("RAG_MIN_SCORE", 0.01),
        db_limit=_int_env("RAG_DB_LIMIT", 5000),
        embedding_provider=_env("RAG_EMBEDDING_PROVIDER", "hash").strip().lower(),
        embedding_model=_env("RAG_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"),
        db_host=_env("DB_HOST", "localhost"),
        db_port=_env("DB_PORT", "5432"),
        db_name=_env("DB_NAME", "govcontracts"),
        db_user=_env("DB_USER", "postgres"),
        db_password=_env("DB_PASSWORD", ""),
        llm_provider=_env("RAG_LLM_PROVIDER", "mock").strip().lower(),
        llm_model=_env("RAG_LLM_MODEL", "mock-contract-rag"),
        llm_base_url=_env("RAG_LLM_BASE_URL", "").rstrip("/"),
        llm_api_key=_env("RAG_LLM_API_KEY", _env("OPENAI_API_KEY", "")),
        temperature=_float_env("RAG_TEMPERATURE", 0.2),
        max_tokens=_int_env("RAG_MAX_TOKENS", 900),
        request_timeout=_int_env("RAG_REQUEST_TIMEOUT", 60),
    )
