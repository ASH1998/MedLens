from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from medlens.artifacts.build_evidence import build_evidence_db
from medlens.artifacts.build_normalization import build_normalization_db
from medlens.tools.local_safety import MedicationSafetyStore


class LocalSafetyToolsTest(unittest.TestCase):
    def test_normalizes_aliases_and_reports_unresolved_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            normalization_db, evidence_db = self._build_fixture_artifacts(Path(tmpdir))
            store = MedicationSafetyStore(normalization_db, evidence_db)

            result = store.normalize_medication_names(["Paracetamol", "Salbutamol", "Mystery Pill"])

            self.assertEqual(result[0].canonical_name, "acetaminophen")
            self.assertEqual(result[1].canonical_name, "albuterol")
            self.assertFalse(result[2].resolved)
            self.assertEqual(result[2].normalized_input, "mystery pill")

    def test_lookup_known_interaction_resolves_aliases_before_pair_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            normalization_db, evidence_db = self._build_fixture_artifacts(Path(tmpdir))
            store = MedicationSafetyStore(normalization_db, evidence_db)

            interaction = store.lookup_known_interaction("Advil", "Warfarin")

            self.assertTrue(interaction.found)
            self.assertEqual((interaction.drug_a, interaction.drug_b), ("ibuprofen", "warfarin"))
            self.assertEqual(interaction.severity, "Major")
            self.assertEqual(interaction.row_count, 2)
            self.assertEqual(interaction.source_regions, ("us",))
            self.assertEqual([effect.adverse_effect for effect in interaction.effects], ["gastrointestinal bleeding", "hematuria"])
            self.assertEqual(interaction.evidence_source, "ddi_reference")
            self.assertEqual(len(interaction.raw_signals), 2)
            self.assertEqual(interaction.raw_signals[0].source_signal_id, "x1")
            self.assertEqual(interaction.raw_signals[0].drug1_raw, "warfarin")
            self.assertEqual(interaction.raw_signals[0].drug2_raw, "ibuprofen")

    def test_lookup_known_interaction_returns_not_found_for_unresolved_drug(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            normalization_db, evidence_db = self._build_fixture_artifacts(Path(tmpdir))
            store = MedicationSafetyStore(normalization_db, evidence_db)

            interaction = store.lookup_known_interaction("Warfarin", "Mystery Pill")

            self.assertFalse(interaction.found)
            self.assertEqual((interaction.drug_a, interaction.drug_b), ("mystery pill", "warfarin"))

    def test_build_structured_report_ranks_findings_and_tracks_limitations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            normalization_db, evidence_db = self._build_fixture_artifacts(Path(tmpdir))
            store = MedicationSafetyStore(normalization_db, evidence_db)

            report = store.build_structured_report(["Warfarin", "Advil", "Paracetamol", "Mystery Pill"])

            self.assertEqual(report.input_medications, ("Warfarin", "Advil", "Paracetamol", "Mystery Pill"))
            self.assertEqual(report.checked_pair_count, 3)
            self.assertEqual(report.overall_severity, "Major")
            self.assertEqual(report.evidence_status, "verified_reference_findings_with_unresolved_inputs")
            self.assertEqual(len(report.findings), 2)
            self.assertEqual((report.findings[0].drug_a, report.findings[0].drug_b), ("ibuprofen", "warfarin"))
            self.assertEqual((report.findings[1].drug_a, report.findings[1].drug_b), ("acetaminophen", "warfarin"))
            self.assertEqual([item.input_name for item in report.unresolved_medications], ["Mystery Pill"])
            self.assertTrue(any("Mystery Pill" in limitation for limitation in report.limitations))

    def test_build_structured_report_deduplicates_resolved_medications(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            normalization_db, evidence_db = self._build_fixture_artifacts(Path(tmpdir))
            store = MedicationSafetyStore(normalization_db, evidence_db)

            report = store.build_structured_report(["Advil", "ibuprofen", "Warfarin"])

            self.assertEqual(report.checked_pair_count, 1)
            self.assertEqual(len(report.findings), 1)

    def test_build_structured_report_handles_no_resolved_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            normalization_db, evidence_db = self._build_fixture_artifacts(Path(tmpdir))
            store = MedicationSafetyStore(normalization_db, evidence_db)

            report = store.build_structured_report(["Mystery Pill"])

            self.assertEqual(report.checked_pair_count, 0)
            self.assertEqual(report.overall_severity, "None")
            self.assertEqual(report.evidence_status, "insufficient_resolved_medications")
            self.assertTrue(any("Fewer than two medications" in limitation for limitation in report.limitations))

    def _build_fixture_artifacts(self, root: Path) -> tuple[Path, Path]:
        normalization_db = root / "normalization.sqlite"
        evidence_db = root / "evidence.sqlite"
        ddi_dir = root / "DDI"
        ddi_dir.mkdir()
        build_normalization_db(normalization_db)
        self._write_usa_fixture(ddi_dir / "usa_prioritized_ddi_ade_signals.csv")
        build_evidence_db(ddi_dir, normalization_db, evidence_db)
        return normalization_db, evidence_db

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
            },
            {
                "interaction_id": "x2",
                "drug1": "ibuprofen",
                "drug2": "warfarin",
                "adverse_effect": "hematuria",
                "severity": "high",
                "mechanism_or_rationale": "additive bleeding",
                "patient_risk_flags_us": "ckd",
                "evidence_basis": "class interaction",
                "source_basis": "DailyMed",
                "source_urls": "https://example.test/a",
                "dataset_type": "screening/reference",
                "use_case_note": "screening only",
            },
            {
                "interaction_id": "x3",
                "drug1": "acetaminophen",
                "drug2": "warfarin",
                "adverse_effect": "inr variability",
                "severity": "medium",
                "mechanism_or_rationale": "monitoring signal",
                "patient_risk_flags_us": "high dose acetaminophen",
                "evidence_basis": "screening signal",
                "source_basis": "DailyMed",
                "source_urls": "https://example.test/c",
                "dataset_type": "screening/reference",
                "use_case_note": "screening only",
            },
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
