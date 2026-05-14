from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from medlens.artifacts.build_evidence import compact_evidence_db
from medlens.tools.local_safety import MedicationSafetyStore
from tests.fixture_artifacts import build_fixture_store


class LocalSafetyToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.store = build_fixture_store(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_normalization_lookup_and_report_core_flow(self) -> None:
        result = self.store.normalize_medication_names(["Paracetamol", "Salbutamol", "Mystery Pill"])
        self.assertEqual(result[0].canonical_name, "acetaminophen")
        self.assertEqual(result[1].canonical_name, "albuterol")
        self.assertFalse(result[2].resolved)

        interaction = self.store.lookup_known_interaction("Advil", "Warfarin")
        self.assertTrue(interaction.found)
        self.assertEqual((interaction.drug_a, interaction.drug_b), ("ibuprofen", "warfarin"))
        self.assertEqual(interaction.severity, "Major")
        self.assertEqual([effect.adverse_effect for effect in interaction.effects], ["gastrointestinal bleeding", "hematuria"])

        report = self.store.build_structured_report(["Warfarin", "Advil", "Paracetamol", "Mystery Pill"])
        self.assertEqual(report.overall_severity, "Major")
        self.assertEqual(report.evidence_status, "verified_reference_findings_with_unresolved_inputs")
        self.assertEqual([(item.drug_a, item.drug_b) for item in report.findings], [("ibuprofen", "warfarin"), ("acetaminophen", "warfarin")])
        self.assertEqual([item.input_name for item in report.unresolved_medications], ["Mystery Pill"])

    def test_interaction_searches_cover_filters_and_mechanism(self) -> None:
        _norm, captopril_major = self.store.list_interactions_for_drug("captopril", min_severity="Major", risk_flag="ckd")
        self.assertEqual([(item.drug_a, item.drug_b) for item in captopril_major], [("captopril", "ibuprofen")])

        bleeding = self.store.search_interactions(effect="bleeding", min_severity="Major")
        self.assertEqual([(item.drug_a, item.drug_b) for item in bleeding.interactions], [("ibuprofen", "warfarin")])

        anchored = self.store.search_interactions(drug="Advil", effect="bleeding")
        self.assertEqual(anchored.drug_normalization.canonical_name, "ibuprofen")
        self.assertEqual([(item.drug_a, item.drug_b) for item in anchored.interactions], [("ibuprofen", "warfarin")])

        mechanism = self.store.search_interactions_by_mechanism("renal", drug="captopril")
        self.assertEqual([(item["drug_a"], item["drug_b"]) for item in mechanism["matches"]], [("captopril", "ibuprofen")])
        self.assertIn("note", mechanism)

    def test_common_medicine_alias_category_and_bulk_tools(self) -> None:
        profile = self.store.get_common_medicine_profile("Dolo")
        self.assertEqual(profile["normalized"]["canonical_name"], "acetaminophen")
        self.assertIn("500 mg", profile["matches"][0]["composition_or_strength_pattern"])

        filtered_common = self.store.search_common_medicines(otc_or_rx="OTC", risk_flag="liver")
        self.assertEqual(filtered_common[0]["canonical_name"], "acetaminophen")

        aliases = self.store.list_aliases_for_drug("Advil")
        self.assertEqual(aliases["drug"]["canonical_name"], "ibuprofen")
        self.assertIn("ibuprofen", aliases["by_type"]["canonical"])

        categories = self.store.list_drugs_by_category("anticoagulant_antiplatelet")
        self.assertIn("warfarin", [item["canonical_name"] for item in categories["drugs"]])

        bulk = self.store.bulk_check_pairs(["Advil", "Mystery Pill"], ["Warfarin"])
        self.assertEqual(bulk["overall_severity"], "Major")
        by_input = {row["candidate"]: row for row in bulk["candidates"]}
        self.assertEqual(by_input["Advil"]["highest_severity"], "Major")
        self.assertTrue(by_input["Mystery Pill"]["unresolved"])

    def test_common_painkiller_combo_gets_practical_calibration(self) -> None:
        normalized = self.store.normalize_medication_names(["Aldigesic-SP Forte", "Crocin"])
        self.assertEqual(
            [item.canonical_name for item in normalized],
            ["aceclofenac", "acetaminophen", "serratiopeptidase", "acetaminophen"],
        )
        self.assertEqual(
            [item.canonical_name for item in self.store.normalize_medication_names(["aldjgesic-sp"])],
            ["aceclofenac", "acetaminophen", "serratiopeptidase"],
        )
        self.assertEqual(
            [item.canonical_name for item in self.store.normalize_medication_names(["it is aldigesic-sp"])],
            ["aceclofenac", "acetaminophen", "serratiopeptidase"],
        )

        report = self.store.build_structured_report(["Aldigesic-SP Forte", "Crocin"])
        self.assertEqual(report.overall_severity, "Major")
        duplicate = report.duplicate_ingredient_warnings[0]
        self.assertEqual(duplicate.ingredient, "acetaminophen")
        self.assertEqual(duplicate.practical_risk_tier, "duplicate_dose_risk")

        guidance_by_pair = {
            (finding.drug_a, finding.drug_b): finding.practical_guidance
            for finding in report.findings
        }
        guidance = guidance_by_pair[("aceclofenac", "acetaminophen")]
        self.assertIsNotNone(guidance)
        self.assertEqual(guidance.practical_risk_tier, "usually_ok_with_limits")

    def test_high_risk_nsaid_blood_thinner_stays_high_concern(self) -> None:
        interaction = self.store.lookup_known_interaction("Advil", "Warfarin")
        self.assertEqual(interaction.severity, "Major")
        self.assertIsNotNone(interaction.practical_guidance)
        self.assertEqual(interaction.practical_guidance.practical_risk_tier, "avoid_or_check_first")

    def test_evidence_metadata_tools(self) -> None:
        sources = self.store.list_evidence_sources()
        issues = self.store.list_import_issues(query="unknown")
        self.assertEqual(sources[0]["source_file"], "usa_prioritized_ddi_ade_signals.csv")
        self.assertEqual(sources[0]["rows_seen"], 7)
        self.assertEqual(issues[0]["reason"], "drug1_unresolved")

    def test_compact_evidence_artifact_preserves_raw_evidence(self) -> None:
        root = Path(self._tmp.name)
        compact_db = root / "evidence.mobile.sqlite"
        compact_evidence_db(root / "evidence.sqlite", compact_db)
        compact_store = MedicationSafetyStore(root / "normalization.sqlite", compact_db)

        interaction = compact_store.lookup_known_interaction("Advil", "Warfarin")
        self.assertTrue(interaction.found)
        self.assertEqual(interaction.raw_signals[0].source_urls, "https://example.test/a")

        report = compact_store.build_structured_report(["Warfarin", "Advil"])
        self.assertEqual(report.overall_severity, "Major")
        self.assertEqual(compact_store.list_import_issues(query="unknown")[0]["reason"], "drug1_unresolved")
        self.assertEqual(
            compact_store.search_interactions_by_mechanism("renal", drug="captopril")["matches"][0]["drug_b"],
            "ibuprofen",
        )


if __name__ == "__main__":
    unittest.main()
