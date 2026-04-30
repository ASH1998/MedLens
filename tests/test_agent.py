from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from medlens.agent import (
    BedrockProvider,
    MedicationSafetyAgent,
    TemplateProvider,
    _bedrock_model_invoke_uris,
    build_provider,
)
from medlens.artifacts.build_evidence import build_evidence_db
from medlens.artifacts.build_normalization import build_normalization_db
from medlens.cli import main
from medlens.tools.local_safety import MedicationSafetyStore


class CapturingProvider:
    name = "capturing"

    def __init__(self) -> None:
        self.system_prompt = ""
        self.user_prompt = ""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        return "Grounded answer from fake model."


class AgentTest(unittest.TestCase):
    def test_agent_passes_structured_report_to_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            normalization_db, evidence_db = self._build_fixture_artifacts(Path(tmpdir))
            provider = CapturingProvider()
            agent = MedicationSafetyAgent(MedicationSafetyStore(normalization_db, evidence_db), provider)

            result = agent.answer(["Advil", "Warfarin", "Mystery Pill"], question="What should I watch for?")

            self.assertEqual(result.response, "Grounded answer from fake model.")
            self.assertEqual(result.provider_name, "capturing")
            self.assertIn("Use only the structured MedLens report", provider.system_prompt)
            self.assertIn("Mystery Pill", provider.user_prompt)
            self.assertIn("ibuprofen", provider.user_prompt)
            self.assertIn("warfarin", provider.user_prompt)
            self.assertEqual(result.report.overall_severity, "Major")

    def test_template_provider_returns_offline_explanation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            normalization_db, evidence_db = self._build_fixture_artifacts(Path(tmpdir))
            agent = MedicationSafetyAgent(MedicationSafetyStore(normalization_db, evidence_db), TemplateProvider())

            result = agent.answer(["Advil", "Warfarin"])

            self.assertIn("Overall local evidence severity: Major", result.response)
            self.assertIn("ibuprofen + warfarin", result.response)
            self.assertIn("local screening output", result.response)

    def test_build_provider_can_use_dotenv_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("GOOGLE_API_KEY=test-key\nGOOGLE_MODEL=test-model\n", encoding="utf-8")

            provider = build_provider("template", env_path=env_path)

            self.assertEqual(provider.name, "template")

    def test_build_provider_can_create_bedrock_provider_from_dotenv_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "AWS_ACCESS_KEY_ID=test-access",
                        "AWS_SECRET_ACCESS_KEY=test-secret",
                        "AWS_REGION=us-west-2",
                        "CLAUDE_MODEL=anthropic.claude-3-5-haiku-20241022-v1:0",
                    ]
                ),
                encoding="utf-8",
            )

            provider = build_provider("bedrock", env_path=env_path)

            self.assertIsInstance(provider, BedrockProvider)
            self.assertEqual(provider.name, "bedrock")

    def test_bedrock_model_uri_uses_single_request_and_double_canonical_encoding(self) -> None:
        request_uri, canonical_uri = _bedrock_model_invoke_uris("global.anthropic.claude-haiku-4-5-20251001-v1:0")

        self.assertEqual(request_uri, "/model/global.anthropic.claude-haiku-4-5-20251001-v1%3A0/invoke")
        self.assertEqual(canonical_uri, "/model/global.anthropic.claude-haiku-4-5-20251001-v1%253A0/invoke")

    def test_cli_agent_json_outputs_response_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            normalization_db, evidence_db = self._build_fixture_artifacts(Path(tmpdir))
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "--format",
                        "agent-json",
                        "--provider",
                        "template",
                        "--normalization-db",
                        str(normalization_db),
                        "--evidence-db",
                        str(evidence_db),
                        "Advil",
                        "Warfarin",
                    ]
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["provider"], "template")
            self.assertIn("ibuprofen + warfarin", payload["response"])
            self.assertEqual(payload["report"]["overall_severity"], "Major")

    def test_cli_chat_runs_interactive_template_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            normalization_db, evidence_db = self._build_fixture_artifacts(Path(tmpdir))
            stdout = io.StringIO()

            with redirect_stdout(stdout), patch("builtins.input", side_effect=["Advil, Warfarin", "What should I watch?", "/quit"]):
                exit_code = main(
                    [
                        "--chat",
                        "--provider",
                        "template",
                        "--normalization-db",
                        str(normalization_db),
                        "--evidence-db",
                        str(evidence_db),
                    ]
                )

            output = stdout.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("MedLens terminal chat", output)
            self.assertIn("ibuprofen + warfarin", output)

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
            }
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
