# Terminal RAG Contract Agent

This folder contains a standalone terminal RAG assistant for government contract opportunities. The default path runs offline with bundled sample data, a deterministic hash-vector index, and a mock LLM. Optional PostgreSQL, SQL dump, sentence-transformers, OpenAI-compatible, OpenAI, and Ollama paths are enabled through environment variables.

## Quick Start

```bash
python -m pip install -r requirements.txt
python smoke_test.py
python -m unittest discover -s tests
python chat.py --source sample --rebuild-index
```

Try prompts like:

```text
Find contracts for a small AI cloud vendor.
Which opportunities involve cybersecurity?
Summarize the best software-related matches.
```

Inside chat:

- `:help` shows commands.
- `:sources` prints the last retrieved contracts.
- `:reindex` rebuilds the index.
- `:quit` exits.

## Layout

- `chat.py`: Terminal chat entrypoint. Keeps `python chat.py` as the primary command.
- `index.py`: Vector index build entrypoint. Keeps `python index.py --source sample --rebuild` available.
- `cli/`: Terminal UI implementation.
- `configuration/`: `.env` and environment variable loading into `Settings`.
- `data/`: Data loading package plus bundled JSON fixtures.
- `documents/`: Contract record normalization into indexable documents.
- `indexing/`: Hash and sentence-transformers embedders plus the persistent JSON vector index.
- `retrieval/`: Index loading/rebuilding and score filtering.
- `generation/`: Mock, OpenAI-compatible, official OpenAI, and Ollama LLM providers.
- `rag/`: Retrieval plus generation orchestration and source citation formatting.
- `tests/`: Unit tests and optional semantic retrieval quality tests.

Generated or local-only folders such as `.rag_index/`, `.tmp/`, `__pycache__/`, and `data/ollama_models/` are ignored and can be rebuilt.

## Data Sources

Use sample data when there is no database or API access:

```bash
python chat.py --source sample --rebuild-index
```

Use a local PostgreSQL dump:

```bash
python chat.py --source dump --rebuild-index
```

Use PostgreSQL directly:

```bash
python chat.py --source db --rebuild-index
```

`auto` tries PostgreSQL first, then a configured dump, then sample data:

```bash
python chat.py --source auto
```

## Configuration

Start from `.env.example` for local use or `.env.cloud.example` for cloud/Codex use. Common settings:

```bash
RAG_DATA_SOURCE=auto
RAG_INDEX_PATH=.rag_index/index.json
RAG_DUMP_PATH=contracts.sql
RAG_TOP_K=5
RAG_MIN_SCORE=0.01
RAG_DB_LIMIT=5000
RAG_EMBEDDING_PROVIDER=hash
RAG_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
RAG_LLM_PROVIDER=mock
RAG_LLM_MODEL=mock-contract-rag
RAG_LLM_BASE_URL=
RAG_LLM_API_KEY=
RAG_REQUEST_TIMEOUT=60
```

For database access, set:

```bash
DB_HOST=localhost
DB_PORT=5432
DB_NAME=govcontracts
DB_USER=postgres
DB_PASSWORD=
```

The DB loader performs read-only `SELECT` queries from `postings`.

## Optional Dependencies

The default sample/mock path only needs `requirements.txt`.

```bash
pip install -r requirements-db.txt          # PostgreSQL access with --source db
pip install -r requirements-llm.txt         # live OpenAI-compatible, OpenAI, or Ollama providers
pip install -r requirements-embeddings.txt  # sentence-transformers embeddings
```

`setup_codex_cloud.sh` installs optional groups when these flags are set:

```bash
RAG_INSTALL_DB=1 RAG_INSTALL_LIVE_DEPS=1 RAG_INSTALL_EMBEDDINGS=1 ./setup_codex_cloud.sh
```

## LLM Providers

Default deterministic provider:

```bash
RAG_LLM_PROVIDER=mock
```

Official OpenAI API:

```bash
RAG_LLM_PROVIDER=openai
RAG_LLM_MODEL=gpt-4.1-mini
RAG_LLM_API_KEY=your_key_here
```

OpenAI-compatible endpoint:

```bash
RAG_LLM_PROVIDER=openai-compatible
RAG_LLM_BASE_URL=https://your-provider.example/v1
RAG_LLM_MODEL=Qwen/Qwen2.5-72B-Instruct
RAG_LLM_API_KEY=your_key_here
```

Ollama:

```bash
RAG_LLM_PROVIDER=ollama
RAG_LLM_BASE_URL=http://localhost:11434
RAG_LLM_MODEL=qwen2.5:14b-instruct
```

On Windows, `start_ollama.ps1` can start Ollama with model storage under this folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\start_ollama.ps1
python live_smoke_test.py
```

## Indexing

Build or rebuild the index:

```bash
python index.py --source sample --rebuild
python index.py --source dump --rebuild
python index.py --source db --rebuild
```

The index is stored at `.rag_index/index.json` by default and is ignored by git.

Optional semantic embeddings:

```bash
pip install -r requirements-embeddings.txt
RAG_EMBEDDING_PROVIDER=sentence-transformers
RAG_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
python index.py --source sample --rebuild
```

The sentence-transformers path may need network access on first use to download the model. Keep `RAG_EMBEDDING_PROVIDER=hash` for dependency-light smoke tests.

## Tests

Run the default offline validation:

```bash
python smoke_test.py
python -m unittest discover -s tests
```

Run the optional live LLM smoke test after configuring a non-mock provider:

```bash
python live_smoke_test.py
```

`live_smoke_test.py` prints `SKIP` and exits successfully when provider variables are not configured.

## Integration

Use `rag.RagAgent.ask(question)` as the small integration surface for future API or website work. The answer object includes the answer text, retrieved sources, and index source label.
