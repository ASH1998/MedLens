from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from medlens.artifacts.build_evidence import build_evidence_db
from medlens.artifacts.build_normalization import build_normalization_db
from medlens.chat.session import ChatSession
from medlens.tools.local_safety import MedicationSafetyStore
from medlens.tools.registry import dispatch, to_bedrock_tools, to_gemini_tools


class ToolRegistryTest(unittest.TestCase):
    def test_schema_adapters_expose_tools(self) -> None:
        self.assertTrue(any(tool["name"] == "build_structured_report" for tool in to_bedrock_tools()))
        self.assertTrue(to_gemini_tools()[0]["functionDeclarations"])

    def test_add_report_consensus_and_effect_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._store(Path(tmpdir))
            session = ChatSession(provider_name="template")

            added = dispatch("add_medications", {"names": ["Advil", "Warfarin"]}, store=store, session=session)
            report = dispatch("build_structured_report", {}, store=store, session=session)
            consensus = dispatch("severity_consensus", {"drug_a": "ibuprofen", "drug_b": "warfarin"}, store=store, session=session)
            effects = dispatch("find_pairs_by_effect", {"effect": "bleeding"}, store=store, session=session)

            self.assertEqual(len(added["added"]), 2)
            self.assertEqual(report["overall_severity"], "Major")
            self.assertEqual(consensus["rolled_up"], "Major")
            self.assertEqual(effects["matches"][0]["drug_a"], "ibuprofen")
            self.assertTrue(session.last_trace)

    def test_unknown_tool_returns_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._store(Path(tmpdir))
            session = ChatSession(provider_name="template")

            result = dispatch("missing", {}, store=store, session=session)

            self.assertEqual(result["code"], "unknown_tool")

    def _store(self, root: Path) -> MedicationSafetyStore:
        normalization_db = root / "normalization.sqlite"
        evidence_db = root / "evidence.sqlite"
        ddi_dir = root / "DDI"
        ddi_dir.mkdir()
        build_normalization_db(normalization_db)
        self._write_usa_fixture(ddi_dir / "usa_prioritized_ddi_ade_signals.csv")
        build_evidence_db(ddi_dir, normalization_db, evidence_db)
        return MedicationSafetyStore(normalization_db, evidence_db)

    def _write_usa_fixture(self, path: Path) -> None:
        fieldnames = [
            "interaction_id",
            "drug1",
            "drug2",
            "adverse_effect",
            "severity",
            "interaction_category",
            "mechanism_or_rationale",
            "interaction_direction",
            "us_population_relevance",
            "patient_risk_flags_us",
            "evidence_basis",
            "source_basis",
            "source_urls",
            "dataset_type",
            "use_case_note",
        ]
        rows = [
            {
                "interaction_id": "x1",
                "drug1": "warfarin",
                "drug2": "ibuprofen",
                "adverse_effect": "gastrointestinal bleeding",
                "severity": "high",
                "mechanism_or_rationale": "additive bleeding",
                "patient_risk_flags_us": "elderly",
                "evidence_basis": "class interaction",
                "source_basis": "DailyMed",
                "source_urls": "https://example.test/a",
                "dataset_type": "screening/reference",
                "use_case_note": "screening only",
            }
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()

