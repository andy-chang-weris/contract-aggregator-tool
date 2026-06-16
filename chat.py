"""Terminal chat entrypoint for the standalone RAG agent."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from configuration import load_settings
from rag import RagAgent


HELP_TEXT = """
Commands:
  :help       Show this help.
  :sources    Show retrieved source records from the last answer.
  :reindex    Rebuild the vector index from the configured data source.
  :quit       Exit.
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chat with the terminal contract RAG agent.")
    parser.add_argument("--source", choices=["auto", "db", "dump", "sample"], help="Contract data source.")
    parser.add_argument("--top-k", type=int, help="Number of contracts to retrieve per question.")
    parser.add_argument("--index-path", help="Path to the JSON vector index.")
    parser.add_argument("--rebuild-index", action="store_true", help="Rebuild the index before starting chat.")
    parser.add_argument("--llm-provider", choices=["mock", "openai", "openai-compatible", "ollama"], help="LLM provider override.")
    return parser.parse_args()


def _print_sources(agent: RagAgent) -> None:
    response = agent.last_response
    if not response or not response.sources:
        print("No sources retrieved yet.")
        return
    for idx, result in enumerate(response.sources, start=1):
        meta = result.metadata
        print(f"{idx}. {meta.get('title', 'Untitled')} | score={result.score:.3f}")
        print(f"   id={meta.get('id') or meta.get('external_id', 'unknown')} agency={meta.get('agency', 'unknown')}")
        print(f"   deadline={meta.get('deadline', 'unknown')} url={meta.get('url', 'none')}")


def main() -> int:
    args = parse_args()
    settings = load_settings().with_overrides(
        data_source=args.source,
        top_k=args.top_k,
        index_path=Path(args.index_path).expanduser() if args.index_path else None,
        llm_provider=args.llm_provider,
    )

    try:
        agent = RagAgent.create(settings, rebuild_index=args.rebuild_index)
    except Exception as exc:
        print("Unable to start the RAG agent.", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("Try: python chat.py --source sample --rebuild-index", file=sys.stderr)
        return 1

    print("Contract RAG terminal agent")
    print(f"Data/index source: {agent.retriever.source_label}")
    print(f"Index path: {settings.index_path}")
    print(f"LLM provider: {settings.llm_provider} ({settings.llm_model})")
    print("Type :help for commands or :quit to exit.")

    while True:
        try:
            question = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            return 0

        if not question:
            continue
        command = question.lower()
        if command in {":quit", ":exit", "quit", "exit"}:
            print("Goodbye.")
            return 0
        if command == ":help":
            print(HELP_TEXT)
            continue
        if command == ":sources":
            _print_sources(agent)
            continue
        if command == ":reindex":
            try:
                agent.rebuild_index()
                print(f"Rebuilt index from {agent.retriever.source_label}.")
            except Exception as exc:
                print(f"Reindex failed: {exc}")
            continue

        try:
            response = agent.ask(question)
        except Exception as exc:
            print(f"Agent error: {exc}")
            continue
        print(f"\nAgent> {response.answer}")


if __name__ == "__main__":
    raise SystemExit(main())

