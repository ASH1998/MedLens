from __future__ import annotations

import unittest

from medlens.chat.commands import (
    clarification_prompt_for_unclear_medications,
    extract_known_medications,
    medication_list_intent,
    pending_clarification_prompt,
    split_medication_names,
    unclear_medication_candidates,
)


class ChatCommandTest(unittest.TestCase):
    def test_split_medication_names_handles_commas_and_and(self) -> None:
        self.assertEqual(split_medication_names("Advil, Warfarin and Paracetamol"), ("Advil", "Warfarin", "Paracetamol"))

    def test_extract_known_medications_prefers_known_alias_phrases(self) -> None:
        aliases = {"warfarin", "advil", "low dose aspirin", "aspirin"}

        extracted = extract_known_medications("I take low dose aspirin and Advil with warfarin.", aliases)

        self.assertIn("low dose aspirin", extracted)
        self.assertIn("advil", extracted)
        self.assertIn("warfarin", extracted)

    def test_medication_list_intent_detects_taking_phrase(self) -> None:
        self.assertTrue(medication_list_intent("I am taking med a along with med b"))
        self.assertFalse(medication_list_intent("My stomach hurts"))

    def test_clarification_prompt_for_unclear_medications(self) -> None:
        aliases = {"dolo 650", "ondansetron"}

        prompt = clarification_prompt_for_unclear_medications("I am taking dolo with ondasetron", aliases, ())

        self.assertIsNotNone(prompt)
        self.assertIn("Please confirm", prompt)

    def test_no_clarification_when_clear_match_exists(self) -> None:
        aliases = {"dolo 650", "ondansetron"}

        prompt = clarification_prompt_for_unclear_medications("I am taking dolo 650 with ondansetron", aliases, ("dolo 650", "ondansetron"))

        self.assertIsNone(prompt)

    def test_partial_match_still_asks_for_unclear_medication(self) -> None:
        aliases = {"dolo 650", "ondansetron"}

        prompt = clarification_prompt_for_unclear_medications("i am taking dolo6 and ondansetron", aliases, ("ondansetron",))
        unclear = unclear_medication_candidates("i am taking dolo6 and ondansetron", aliases, ("ondansetron",))

        self.assertIsNotNone(prompt)
        self.assertEqual(unclear, ("dolo6",))
        self.assertIn("dolo6", prompt)

    def test_pending_clarification_prompt_references_original_unclear_name(self) -> None:
        prompt = pending_clarification_prompt(("dolo6",), ("ondansetron",))

        self.assertIn("dolo6", prompt)
        self.assertIn("ondansetron", prompt)


if __name__ == "__main__":
    unittest.main()
