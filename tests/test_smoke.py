from __future__ import annotations

from pathlib import Path
import sys
import unittest
from uuid import uuid4

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from configuration import AGENT_DIR, load_settings
from data import load_contracts_from_dump, parse_postings_sql
from generation import OpenAICompatibleLLM, make_llm
from rag import RagAgent


class RagAgentSmokeTest(unittest.TestCase):
    def test_sample_mock_agent_returns_grounded_sources(self) -> None:
        scratch_root = AGENT_DIR / ".tmp"
        scratch_root.mkdir(exist_ok=True)
        index_path = scratch_root / f"unit-test-index-{uuid4().hex}.json"
        try:
            settings = load_settings().with_overrides(
                data_source="sample",
                llm_provider="mock",
                index_path=index_path,
                top_k=2,
                min_score=0.0,
                embedding_provider="hash",
            )
            agent = RagAgent.create(settings, rebuild_index=True)
            response = agent.ask("AI cloud software contract")
        finally:
            index_path.unlink(missing_ok=True)
        self.assertTrue(response.sources)
        self.assertIn("retrieved contract records", response.answer)
        self.assertEqual(response.index_source, "sample")

    def test_parse_plain_copy_dump_records(self) -> None:
        sql_text = (
            "COPY public.postings (id, source_site, external_id, url, agency, naics, posted_date, "
            "contract_type, place_of_performance, title, organization, description, deadline, "
            "award_date, contract_value, award_status, acq_strategy, source_listing_id, "
            "date_scraped, raw_response) FROM stdin;\n"
            "1\tsample\tEXT-1\thttps://example.invalid/1\tExample Agency\t541511\t2026-06-01\tRFP\tRemote\t"
            "AI Platform Support\tData Office\tCloud AI analytics\\nAPI work\t2026-07-15\t\\N\t$1M\tOpen\t"
            "Small business\tLIST-1\t2026-06-10\tRaw text\n"
            "\\.\n"
        )

        records = parse_postings_sql(sql_text)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], "1")
        self.assertEqual(records[0]["title"], "AI Platform Support")
        self.assertEqual(records[0]["award_date"], None)
        self.assertIn("API work", records[0]["description"])

    def test_load_plain_copy_dump_file(self) -> None:
        scratch_root = AGENT_DIR / ".tmp"
        scratch_root.mkdir(exist_ok=True)
        dump_path = scratch_root / f"plain-copy-dump-{uuid4().hex}.sql"
        try:
            dump_path.write_text(
                "COPY public.postings (id, source_site, external_id, url, agency, title, description) FROM stdin;\n"
                "7\tdump\tDUMP-7\thttps://example.invalid/7\tDump Agency\tCloud Migration\tCloud support services\n"
                "\\.\n",
                encoding="utf-8",
            )
            settings = load_settings().with_overrides(dump_path=dump_path)

            records = load_contracts_from_dump(settings)
        finally:
            dump_path.unlink(missing_ok=True)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source_site"], "dump")
        self.assertEqual(records[0]["title"], "Cloud Migration")

    def test_openai_provider_defaults_to_official_base_url(self) -> None:
        settings = load_settings().with_overrides(
            llm_provider="openai",
            llm_model="gpt-test",
            llm_api_key="test-key",
            llm_base_url="",
        )

        llm = make_llm(settings)

        self.assertIsInstance(llm, OpenAICompatibleLLM)
        self.assertEqual(llm.base_url, "https://api.openai.com/v1")


if __name__ == "__main__":
    unittest.main()

