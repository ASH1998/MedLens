from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from medlens.chat.session import ChatSession
from medlens.tools.registry import dispatch, to_bedrock_tools, to_gemini_tools
from tests.fixture_artifacts import build_fixture_store


class ToolRegistryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.store = build_fixture_store(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def setUp(self) -> None:
        self.session = ChatSession(provider_name="template")

    def test_schema_adapters_expose_core_tools(self) -> None:
        names = {tool["name"] for tool in to_bedrock_tools()}
        self.assertIn("build_structured_report", names)
        self.assertIn("search_interactions", names)
        self.assertIn("bulk_check_pairs", names)
        self.assertIn("list_aliases_for_drug", names)
        self.assertTrue(to_gemini_tools()[0]["functionDeclarations"])

    def test_session_report_and_pair_dispatch(self) -> None:
        added = dispatch("add_medications", {"names": ["Advil", "Warfarin"]}, store=self.store, session=self.session)
        report = dispatch("build_structured_report", {}, store=self.store, session=self.session)
        consensus = dispatch("severity_consensus", {"drug_a": "ibuprofen", "drug_b": "warfarin"}, store=self.store, session=self.session)

        self.assertEqual(len(added["added"]), 2)
        self.assertEqual(report["overall_severity"], "Major")
        self.assertEqual(consensus["rolled_up"], "Major")
        self.assertTrue(self.session.last_trace)

    def test_new_search_and_catalog_dispatches(self) -> None:
        interactions = dispatch("search_interactions", {"effect": "bleeding", "min_severity": "Major"}, store=self.store, session=self.session)
        self.assertEqual(interactions["count"], 1)
        self.assertEqual((interactions["interactions"][0]["drug_a"], interactions["interactions"][0]["drug_b"]), ("ibuprofen", "warfarin"))

        mechanism = dispatch("search_interactions_by_mechanism", {"query": "renal", "drug": "captopril"}, store=self.store, session=self.session)
        self.assertEqual([(item["drug_a"], item["drug_b"]) for item in mechanism["matches"]], [("captopril", "ibuprofen")])

        bulk = dispatch("bulk_check_pairs", {"candidates": ["Advil", "Mystery Pill"], "against": ["Warfarin"]}, store=self.store, session=self.session)
        self.assertEqual(bulk["overall_severity"], "Major")

        aliases = dispatch("list_aliases_for_drug", {"drug": "Advil"}, store=self.store, session=self.session)
        self.assertEqual(aliases["drug"]["canonical_name"], "ibuprofen")

        common = dispatch("search_common_medicines", {"otc_or_rx": "OTC", "risk_flag": "liver"}, store=self.store, session=self.session)
        self.assertEqual(common["matches"][0]["canonical_name"], "acetaminophen")

    def test_unknown_tool_returns_structured_error(self) -> None:
        result = dispatch("missing", {}, store=self.store, session=self.session)
        self.assertEqual(result["code"], "unknown_tool")


if __name__ == "__main__":
    unittest.main()
