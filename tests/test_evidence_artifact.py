from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from medlens.artifacts.build_evidence import build_evidence_db
from medlens.artifacts.build_normalization import build_normalization_db


class EvidenceArtifactTest(unittest.TestCase):
    def test_build_evidence_db_imports_resolved_pairs_and_tracks_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            normalization_db = root / "normalization.sqlite"
            evidence_db = root / "evidence.sqlite"
            ddi_dir = root / "DDI"
            ddi_dir.mkdir()

            build_normalization_db(normalization_db)
            self._write_usa_fixture(ddi_dir / "usa_prioritized_ddi_ade_signals.csv")

            build_evidence_db(ddi_dir, normalization_db, evidence_db)

            with sqlite3.connect(evidence_db) as conn:
                interaction = conn.execute(
                    """
                    SELECT drug_a, drug_b, severity, row_count
                    FROM known_interaction
                    WHERE drug_a = 'ibuprofen' AND drug_b = 'warfarin'
                    """
                ).fetchone()
                effects = conn.execute("SELECT COUNT(*) FROM known_interaction_effect").fetchone()[0]
                raw_signals = conn.execute("SELECT COUNT(*) FROM ddi_raw_signal").fetchone()[0]
                unresolved_raw = conn.execute("SELECT COUNT(*) FROM ddi_raw_signal WHERE resolved = 0").fetchone()[0]
                linked_raw = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM ddi_raw_signal
                    WHERE known_interaction_id = (
                        SELECT id FROM known_interaction WHERE drug_a = 'ibuprofen' AND drug_b = 'warfarin'
                    )
                    """
                ).fetchone()[0]
                issues = conn.execute("SELECT COUNT(*) FROM ddi_import_issue").fetchone()[0]
                file_stats = conn.execute(
                    """
                    SELECT rows_seen, rows_imported, rows_unresolved, unique_pairs_imported
                    FROM evidence_import_file
                    WHERE source_file = 'usa_prioritized_ddi_ade_signals.csv'
                    """
                ).fetchone()

            self.assertEqual(interaction, ("ibuprofen", "warfarin", "Major", 2))
            self.assertEqual(effects, 2)
            self.assertEqual(raw_signals, 3)
            self.assertEqual(unresolved_raw, 1)
            self.assertEqual(linked_raw, 2)
            self.assertEqual(issues, 1)
            self.assertEqual(file_stats, (3, 2, 1, 1))

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
                "source_urls": "https://example.test/a | https://example.test/b",
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
                "drug1": "unknown drug",
                "drug2": "warfarin",
                "adverse_effect": "bleeding",
                "severity": "high",
            },
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
