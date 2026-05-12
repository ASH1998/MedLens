from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from medlens.artifacts.build_evidence import build_evidence_db
from medlens.artifacts.build_normalization import build_normalization_db
from medlens.cli import main


class CliTest(unittest.TestCase):
    def test_cli_outputs_json_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            normalization_db, evidence_db = self._build_fixture_artifacts(Path(tmpdir))
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--normalization-db",
                        str(normalization_db),
                        "--evidence-db",
                        str(evidence_db),
                        "Advil",
                        "Warfarin",
                        "Mystery Pill",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["overall_severity"], "Major")
            self.assertEqual(payload["evidence_status"], "verified_reference_findings_with_unresolved_inputs")
            self.assertEqual(payload["findings"][0]["drug_a"], "ibuprofen")
            self.assertEqual(payload["findings"][0]["drug_b"], "warfarin")
            self.assertEqual(payload["findings"][0]["evidence_source"], "ddi_reference")
            self.assertEqual(payload["findings"][0]["raw_signals"][0]["source_signal_id"], "x1")
            self.assertEqual(payload["unresolved_medications"][0]["input_name"], "Mystery Pill")

    def test_cli_outputs_text_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            normalization_db, evidence_db = self._build_fixture_artifacts(Path(tmpdir))
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--format",
                        "text",
                        "--normalization-db",
                        str(normalization_db),
                        "--evidence-db",
                        str(evidence_db),
                        "Advil",
                        "Warfarin",
                    ]
                )

            output = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Overall severity: Major", output)
            self.assertIn("ibuprofen + warfarin", output)

    def _build_fixture_artifacts(self, root: Path) -> tuple[Path, Path]:
        normalization_db = root / "normalization.sqlite"
        evidence_db = root / "evidence.sqlite"
        ddi_dir = root / "DDI"
        ddi_dir.mkdir()
        build_normalization_db(normalization_db, common_medicines_csv=None)
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
            }
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
