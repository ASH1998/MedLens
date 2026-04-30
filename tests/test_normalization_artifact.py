from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from medlens.artifacts.build_normalization import build_normalization_db, normalize_lookup_text


class NormalizationArtifactTest(unittest.TestCase):
    def test_normalize_lookup_text_handles_ocr_like_punctuation(self) -> None:
        self.assertEqual(normalize_lookup_text(" Amoxicillin/Clavulanate  "), "amoxicillin clavulanate")
        self.assertEqual(normalize_lookup_text("Dolo-650"), "dolo 650")

    def test_build_artifact_is_idempotent_and_lookup_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "normalization.sqlite"

            build_normalization_db(output)
            build_normalization_db(output)

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

            self.assertGreaterEqual(drug_count, 750)
            self.assertGreaterEqual(alias_count, 1000)
            self.assertEqual(row, ("acetaminophen",))


if __name__ == "__main__":
    unittest.main()
