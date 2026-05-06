from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from medlens.agent import (
    BedrockProvider,
    MedicationSafetyAgent,
    TemplateProvider,
    _bedrock_model_invoke_uris,
    build_provider,
)
from medlens.cli import main
from tests.fixture_artifacts import build_fixture_store


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
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.store = build_fixture_store(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_agent_passes_structured_report_to_provider(self) -> None:
        provider = CapturingProvider()
        agent = MedicationSafetyAgent(self.store, provider)

        result = agent.answer(["Advil", "Warfarin", "Mystery Pill"], question="What should I watch for?")

        self.assertEqual(result.response, "Grounded answer from fake model.")
        self.assertIn("MedLens", provider.system_prompt)
        self.assertIn("Mystery Pill", provider.user_prompt)
        self.assertIn("ibuprofen", provider.user_prompt)
        self.assertEqual(result.report.overall_severity, "Major")

    def test_template_provider_returns_offline_explanation(self) -> None:
        agent = MedicationSafetyAgent(self.store, TemplateProvider())
        result = agent.answer(["Advil", "Warfarin"])

        self.assertIn("Overall local evidence severity: Major", result.response)
        self.assertIn("ibuprofen + warfarin", result.response)

    def test_provider_factory_and_bedrock_uri_are_stable_without_network(self) -> None:
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

        request_uri, canonical_uri = _bedrock_model_invoke_uris("global.anthropic.claude-haiku-4-5-20251001-v1:0")
        self.assertIsInstance(provider, BedrockProvider)
        self.assertEqual(request_uri, "/model/global.anthropic.claude-haiku-4-5-20251001-v1%3A0/invoke")
        self.assertEqual(canonical_uri, "/model/global.anthropic.claude-haiku-4-5-20251001-v1%253A0/invoke")

    def test_cli_agent_json_outputs_response_and_report(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "--format",
                    "agent-json",
                    "--provider",
                    "template",
                    "--normalization-db",
                    str(self.store.normalization_db),
                    "--evidence-db",
                    str(self.store.evidence_db),
                    "Advil",
                    "Warfarin",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["provider"], "template")
        self.assertIn("ibuprofen + warfarin", payload["response"])
        self.assertEqual(payload["report"]["overall_severity"], "Major")


if __name__ == "__main__":
    unittest.main()
