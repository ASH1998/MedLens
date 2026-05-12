from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from medlens.agent import TemplateProvider, ToolCall, ToolModelResponse
from medlens.agent_loop import TOOL_LOOP_SYSTEM_PROMPT, run_agent_turn
from medlens.chat.session import ChatSession
from tests.fixture_artifacts import build_fixture_store


class AgentLoopTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.store = build_fixture_store(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_template_provider_uses_core_report_tools(self) -> None:
        session = ChatSession(provider_name="template")
        result = run_agent_turn(
            provider=TemplateProvider(),
            session=session,
            store=self.store,
            user_message="I take Advil and Warfarin.",
            candidate_medications=("Advil", "Warfarin"),
        )

        self.assertEqual(result.report.overall_severity, "Major")
        self.assertEqual(result.used_tools, ("normalize_medications", "add_medications", "build_structured_report"))
        self.assertIn("ibuprofen + warfarin", result.final_text)
        self.assertIn("https://example.test/a", result.final_text)

    def test_template_provider_routes_common_single_drug_and_debug_questions(self) -> None:
        list_result = run_agent_turn(
            provider=TemplateProvider(),
            session=ChatSession(provider_name="template"),
            store=self.store,
            user_message="what medicines cant be taken with captopril",
        )
        self.assertEqual(list_result.used_tools, ("list_interactions_for_drug",))
        self.assertIn("ibuprofen", list_result.final_text)

        common_result = run_agent_turn(
            provider=TemplateProvider(),
            session=ChatSession(provider_name="template"),
            store=self.store,
            user_message="I take Dolo. what is this used for?",
        )
        self.assertEqual(common_result.used_tools, ("get_common_medicine_profile",))
        self.assertIn("acetaminophen", common_result.final_text)

        sources_result = run_agent_turn(
            provider=TemplateProvider(),
            session=ChatSession(provider_name="template"),
            store=self.store,
            user_message="what evidence sources are loaded?",
        )
        self.assertEqual(sources_result.used_tools, ("list_evidence_sources",))
        self.assertIn("usa_prioritized_ddi_ade_signals.csv", sources_result.final_text)

    def test_explicit_medication_names_report_is_not_overwritten_by_empty_session(self) -> None:
        session = ChatSession(provider_name="scripted")
        result = run_agent_turn(
            provider=ExplicitReportProvider(),
            session=session,
            store=self.store,
            user_message="Check Dolo 650 and ondansetron",
        )

        self.assertIsNotNone(result.report)
        self.assertEqual(result.report.input_medications, ("Dolo 650", "ondansetron"))
        self.assertEqual(result.report.evidence_status, "no_reference_findings")

    def test_prompt_mentions_new_tool_routes(self) -> None:
        self.assertIn("search_interactions", TOOL_LOOP_SYSTEM_PROMPT)
        self.assertIn("search_interactions_by_mechanism", TOOL_LOOP_SYSTEM_PROMPT)
        self.assertIn("bulk_check_pairs", TOOL_LOOP_SYSTEM_PROMPT)
        self.assertIn("list_aliases_for_drug", TOOL_LOOP_SYSTEM_PROMPT)
        self.assertIn("list_drugs_by_category", TOOL_LOOP_SYSTEM_PROMPT)


class ExplicitReportProvider:
    name = "scripted"

    def generate_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ToolModelResponse:
        del system_prompt, tools
        if not any(message.get("role") == "tool" for message in messages):
            return ToolModelResponse(
                text="",
                tool_calls=(ToolCall(id="report-1", name="build_structured_report", args={"medication_names": ["Dolo 650", "ondansetron"]}),),
            )
        return ToolModelResponse(text="Done.")


if __name__ == "__main__":
    unittest.main()
