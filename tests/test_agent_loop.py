from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from medlens.agent import TemplateProvider, ToolCall, ToolModelResponse
from medlens.agent_loop import run_agent_turn
from medlens.artifacts.build_evidence import build_evidence_db
from medlens.artifacts.build_normalization import build_normalization_db
from medlens.chat.session import ChatSession
from medlens.tools.local_safety import MedicationSafetyStore


class AgentLoopTest(unittest.TestCase):
    def test_template_provider_uses_native_sqlite_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._store(Path(tmpdir))
            session = ChatSession(provider_name="template")

            result = run_agent_turn(
                provider=TemplateProvider(),
                session=session,
                store=store,
                user_message="I take Advil and Warfarin.",
                candidate_medications=("Advil", "Warfarin"),
            )

            self.assertEqual(result.report.overall_severity, "Major")
            self.assertIn("normalize_medications", result.used_tools)
            self.assertIn("add_medications", result.used_tools)
            self.assertIn("build_structured_report", result.used_tools)
            self.assertIn("ibuprofen + warfarin", result.final_text)
            self.assertIn("Source:", result.final_text)
            self.assertIn("https://example.test/a", result.final_text)
            self.assertEqual(session.medication_inputs(), ("Advil", "Warfarin"))

    def test_explicit_medication_names_report_is_not_overwritten_by_empty_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._store(Path(tmpdir))
            session = ChatSession(provider_name="scripted")

            result = run_agent_turn(
                provider=ExplicitReportProvider(),
                session=session,
                store=store,
                user_message="Check Dolo 650 and ondansetron",
            )

            self.assertIsNotNone(result.report)
            self.assertEqual(result.report.input_medications, ("Dolo 650", "ondansetron"))
            self.assertEqual(result.report.checked_pair_count, 1)
            self.assertEqual(result.report.evidence_status, "no_reference_findings")

    def test_existing_session_builds_report_with_native_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._store(Path(tmpdir))
            session = ChatSession(provider_name="template")
            session.medications = store.normalize_medication_names(("Advil", "Warfarin"))

            result = run_agent_turn(
                provider=TemplateProvider(),
                session=session,
                store=store,
                user_message="What should I watch for?",
            )

            self.assertEqual(result.used_tools, ("build_structured_report",))
            self.assertIn("gastrointestinal bleeding", result.final_text)

    def test_template_provider_searches_aliases_and_asks_clarification(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._store(Path(tmpdir))
            session = ChatSession(provider_name="template")

            result = run_agent_turn(
                provider=TemplateProvider(),
                session=session,
                store=store,
                user_message="i am taking dolo6 and ondansetron",
            )

            self.assertIsNone(result.report)
            self.assertIn("normalize_medications", result.used_tools)
            self.assertIn("search_drug_aliases", result.used_tools)
            self.assertIn("dolo6", result.final_text)
            self.assertIn("ondansetron", result.final_text)
            self.assertNotIn("build_structured_report", result.used_tools)

    def test_template_provider_uses_previous_clarification_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._store(Path(tmpdir))
            session = ChatSession(provider_name="template")

            first = run_agent_turn(
                provider=TemplateProvider(),
                session=session,
                store=store,
                user_message="i am taking dolo6 and ondansetron",
            )
            second = run_agent_turn(
                provider=TemplateProvider(),
                session=session,
                store=store,
                user_message="its a brand name",
            )

            self.assertIn("dolo6", first.final_text)
            self.assertIn("still need the exact medicine name", second.final_text)
            self.assertEqual(second.used_tools, ())

    def test_template_provider_merges_clarified_name_with_recognized_med(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = self._store(Path(tmpdir))
            session = ChatSession(provider_name="template")

            run_agent_turn(
                provider=TemplateProvider(),
                session=session,
                store=store,
                user_message="i am taking dolo6 and ondansetron",
            )
            result = run_agent_turn(
                provider=TemplateProvider(),
                session=session,
                store=store,
                user_message="its Dolo 650",
            )

            self.assertEqual(session.medication_inputs(), ("ondansetron", "Dolo 650"))
            self.assertIn("build_structured_report", result.used_tools)

    def test_template_provider_default_answer_is_concise(self) -> None:
        session = ChatSession(provider_name="template")
        result = run_agent_turn(
            provider=TemplateProvider(),
            session=session,
            store=MedicationSafetyStore(),
            user_message="am taking acetaminophen ondansetron fluorouracil azithromycin",
        )

        self.assertLessEqual(len(result.final_text.splitlines()), 8)
        self.assertNotIn("CRITICAL FINDINGS", result.final_text)
        self.assertNotIn("Mechanism:", result.final_text)
        self.assertNotIn("Patient risk factors", result.final_text)
        self.assertNotIn("Immediate Recommendations", result.final_text)
        self.assertIn("Ask for details", result.final_text)

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
                tool_calls=(
                    ToolCall(
                        id="report-1",
                        name="build_structured_report",
                        args={"medication_names": ["Dolo 650", "ondansetron"]},
                    ),
                ),
            )
        return ToolModelResponse(text="Done.")


if __name__ == "__main__":
    unittest.main()
