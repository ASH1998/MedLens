from __future__ import annotations

import sqlite3
import tempfile
import unittest
import csv
from pathlib import Path

from medlens.artifacts.build_normalization import build_normalization_db, normalize_lookup_text


class NormalizationArtifactTest(unittest.TestCase):
    def test_normalize_lookup_text_handles_ocr_like_punctuation(self) -> None:
        self.assertEqual(normalize_lookup_text(" Amoxicillin/Clavulanate  "), "amoxicillin clavulanate")
        self.assertEqual(normalize_lookup_text("Dolo-650"), "dolo 650")

    def test_build_artifact_is_idempotent_and_lookup_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "normalization.sqlite"

            build_normalization_db(output, common_medicines_csv=None)
            build_normalization_db(output, common_medicines_csv=None)

            with sqlite3.connect(output) as conn:
                drug_count = conn.execute("SELECT COUNT(*) FROM drug").fetchone()[0]
                alias_count = conn.execute("SELECT COUNT(*) FROM drug_alias").fetchone()[0]
                row = conn.execute(
                    """
                    SELECT d.canonical_name
                    FROM drug_alias a
                    JOIN drug d ON d.id = a.drug_id
                    WHERE a.normalized_alias = ?
                    """,
                    ("paracetamol",),
                ).fetchone()
                dolo650 = conn.execute(
                    """
                    SELECT d.canonical_name
                    FROM drug_alias a
                    JOIN drug d ON d.id = a.drug_id
                    WHERE a.normalized_alias = ?
                    """,
                    ("dolo650",),
                ).fetchone()

            self.assertGreaterEqual(drug_count, 750)
            self.assertGreaterEqual(alias_count, 1000)
            self.assertEqual(row, ("acetaminophen",))
            self.assertEqual(dolo650, ("acetaminophen",))

    def test_build_artifact_imports_india_common_medicines_for_alias_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output = root / "normalization.sqlite"
            common_csv = root / "common_medicines_india_dataset_5000.csv"
            self._write_common_medicines_fixture(common_csv)

            build_normalization_db(output, common_medicines_csv=common_csv)

            with sqlite3.connect(output) as conn:
                common_count = conn.execute("SELECT COUNT(*) FROM india_common_medicine").fetchone()[0]
                dolo = conn.execute(
                    """
                    SELECT d.canonical_name
                    FROM drug_alias a
                    JOIN drug d ON d.id = a.drug_id
                    WHERE a.normalized_alias = 'dolo'
                    """
                ).fetchone()
                synonym = conn.execute(
                    """
                    SELECT d.canonical_name
                    FROM drug_alias a
                    JOIN drug d ON d.id = a.drug_id
                    WHERE a.normalized_alias = 'acetaminophen'
                    """
                ).fetchone()
                common_brand_dose = conn.execute(
                    """
                    SELECT d.canonical_name
                    FROM drug_alias a
                    JOIN drug d ON d.id = a.drug_id
                    WHERE a.normalized_alias = 'dolo500'
                    """
                ).fetchone()
                component_alias = conn.execute(
                    "SELECT COUNT(*) FROM drug_alias WHERE normalized_alias = 'combiflam'"
                ).fetchone()[0]

            self.assertEqual(common_count, 2)
            self.assertEqual(dolo, ("acetaminophen",))
            self.assertEqual(synonym, ("acetaminophen",))
            self.assertEqual(common_brand_dose, ("acetaminophen",))
            self.assertEqual(component_alias, 0)

    def test_build_artifact_imports_brand_ingredient_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output = root / "normalization.sqlite"
            brand_map = root / "india_common_brand_ingredient_map.csv"
            self._write_brand_ingredient_fixture(brand_map)

            build_normalization_db(output, common_medicines_csv=None, brand_ingredient_map_csv=brand_map)

            with sqlite3.connect(output) as conn:
                rows = conn.execute(
                    """
                    SELECT d.canonical_name
                    FROM medicine_ingredient_map m
                    JOIN drug d ON d.id = m.ingredient_drug_id
                    WHERE m.normalized_brand_name = 'aldigesic sp'
                    ORDER BY m.ingredient_order
                    """
                ).fetchall()

            self.assertEqual(
                rows,
                [("aceclofenac",), ("acetaminophen",), ("serratiopeptidase",)],
            )

    def _write_common_medicines_fixture(self, path: Path) -> None:
        fieldnames = [
            "medicine_id",
            "generic_or_common_name",
            "composition_or_strength_pattern",
            "dosage_form",
            "therapeutic_category",
            "common_daily_life_use_india",
            "common_brand_examples_india",
            "availability_context_india",
            "otc_or_rx",
            "nlem_or_jan_aushadhi_presence",
            "india_relevance",
            "patient_risk_flags_india",
            "source_basis",
            "source_urls",
            "dataset_note",
        ]
        rows = [
            {
                "medicine_id": "MEDIND-1",
                "generic_or_common_name": "Paracetamol / Acetaminophen",
                "composition_or_strength_pattern": "500 mg tablet",
                "dosage_form": "Tablet",
                "therapeutic_category": "Analgesic / antipyretic",
                "common_daily_life_use_india": "Fever",
                "common_brand_examples_india": "Dolo, Calpol",
                "availability_context_india": "Common",
                "otc_or_rx": "OTC/Rx",
                "nlem_or_jan_aushadhi_presence": "NLEM",
                "india_relevance": "High",
                "patient_risk_flags_india": "Liver disease",
                "source_basis": "fixture",
                "source_urls": "https://example.test",
                "dataset_note": "fixture",
            },
            {
                "medicine_id": "MEDIND-2",
                "generic_or_common_name": "Ibuprofen",
                "composition_or_strength_pattern": "200 mg tablet",
                "dosage_form": "Tablet",
                "therapeutic_category": "NSAID analgesic",
                "common_daily_life_use_india": "Pain",
                "common_brand_examples_india": "Brufen, Combiflam component",
                "availability_context_india": "Common",
                "otc_or_rx": "OTC/Rx",
                "nlem_or_jan_aushadhi_presence": "NLEM",
                "india_relevance": "High",
                "patient_risk_flags_india": "Kidney disease",
                "source_basis": "fixture",
                "source_urls": "https://example.test",
                "dataset_note": "fixture",
            },
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _write_brand_ingredient_fixture(self, path: Path) -> None:
        fieldnames = [
            "brand_name",
            "active_ingredients",
            "strengths",
            "region",
            "source_basis",
            "source_urls",
            "notes",
        ]
        rows = [
            {
                "brand_name": "Aldigesic SP",
                "active_ingredients": "aceclofenac|acetaminophen|serratiopeptidase",
                "strengths": "100 mg|325 mg|10 mg",
                "region": "india",
                "source_basis": "fixture",
                "source_urls": "https://example.test/aldigesic",
                "notes": "fixture",
            },
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
