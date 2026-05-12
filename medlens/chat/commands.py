"""Slash command handling for MedLens terminal chat."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from medlens.artifacts.build_normalization import normalize_lookup_text
from medlens.chat.session import ChatSession
from medlens.tools.local_safety import MedicationSafetyStore
from medlens.tools.registry import dispatch


HELP_TEXT = """Commands:
/meds - show current medications
/meds aspirin, warfarin - replace the medication list
/add ibuprofen - add medications
/remove ibuprofen - remove medications
/check or /report - rerun the structured report
/why <drug a> <drug b> - show severity consensus
/sources - show evidence provenance
/trace - show previous tool trace
/clear - clear medications and transcript
/provider - show provider and privacy mode
/help - show this help
/quit - exit
"""


@dataclass(frozen=True)
class CommandResult:
    kind: str
    payload: dict[str, object] | list[object] | str | None = None
    should_exit: bool = False


def split_medication_names(value: str) -> tuple[str, ...]:
    normalized = re.sub(r"\s+\band\b\s+", ",", value, flags=re.IGNORECASE)
    reader = csv.reader(io.StringIO(normalized), skipinitialspace=True)
    return tuple(item.strip() for row in reader for item in row if item.strip())


def handle_command(message: str, *, store: MedicationSafetyStore, session: ChatSession) -> CommandResult:
    command, _, tail = message.strip().partition(" ")
    command = command.casefold()
    tail = tail.strip()

    if command in {"/q", "/quit", "/exit"}:
        return CommandResult("quit", should_exit=True)
    if command == "/help":
        return CommandResult("help", HELP_TEXT)
    if command == "/trace":
        return CommandResult("trace", [record.__dict__ for record in session.last_trace])
    if command == "/provider":
        return CommandResult("tool", dispatch("current_session_summary", {}, store=store, session=session))
    if command == "/sources":
        return CommandResult("tool", dispatch("evidence_about", {"topic": "sources"}, store=store, session=session))
    if command == "/clear":
        session.transcript.clear()
        return CommandResult("tool", dispatch("clear_medications", {}, store=store, session=session))
    if command == "/meds":
        if tail:
            dispatch("clear_medications", {}, store=store, session=session)
            return CommandResult("tool", dispatch("add_medications", {"names": split_medication_names(tail)}, store=store, session=session))
        return CommandResult("medications", dispatch("list_medications", {}, store=store, session=session))
    if command == "/add":
        return CommandResult("tool", dispatch("add_medications", {"names": split_medication_names(tail)}, store=store, session=session))
    if command == "/remove":
        return CommandResult("tool", dispatch("remove_medications", {"names": split_medication_names(tail)}, store=store, session=session))
    if command in {"/check", "/report"}:
        return CommandResult("report", dispatch("build_structured_report", {}, store=store, session=session))
    if command == "/why":
        names = split_medication_names(tail)
        if len(names) < 2:
            return CommandResult("error", "Usage: /why <drug a>, <drug b>")
        return CommandResult("tool", dispatch("severity_consensus", {"drug_a": names[0], "drug_b": names[1]}, store=store, session=session))
    return CommandResult("error", f"Unknown command: {command}. Type /help.")


def extract_known_medications(message: str, aliases: set[str]) -> tuple[str, ...]:
    """Extract known aliases from short natural-language terminal messages."""
    words = re.findall(r"[A-Za-z0-9]+", message.casefold())
    found: list[str] = []
    seen: set[str] = set()
    max_window = min(5, len(words))
    for size in range(max_window, 0, -1):
        for idx in range(0, len(words) - size + 1):
            phrase = " ".join(words[idx : idx + size])
            if phrase in aliases and phrase not in seen:
                found.append(phrase)
                seen.add(phrase)
    return tuple(found)


def medication_list_intent(message: str) -> bool:
    """Return true when a message likely contains a medication list."""
    normalized = message.casefold()
    patterns = (
        r"\bi\s*(am|'m)?\s*taking\b",
        r"\bi\s*take\b",
        r"\bmy\s+meds?\b",
        r"\bmedications?\b",
        r"\balong\s+with\b",
        r"\bwith\b.+\b(and|,)\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def clarification_prompt_for_unclear_medications(message: str, aliases: set[str], matches: tuple[str, ...]) -> str | None:
    """Ask a focused clarification question when extraction is absent or weak."""
    if not medication_list_intent(message):
        return None
    unclear = unclear_medication_candidates(message, aliases, matches)
    if matches and not unclear:
        return None

    candidates = unclear or _possible_medication_phrases(message)
    if not candidates:
        return (
            "I could not identify the medication names clearly. Please list the medicines exactly as written, "
            "separated by commas, for example: /add Dolo 650, ondansetron."
        )

    suggestions: list[str] = []
    for candidate in candidates[:4]:
        candidate_norm = normalize_lookup_text(candidate)
        partials = sorted(alias for alias in aliases if candidate_norm and (candidate_norm in alias or alias in candidate_norm))
        if partials:
            suggestions.append(f"{candidate}: possible match {partials[0]}")

    if suggestions:
        return (
            "I found possible medication text, but it is not clear enough to check safely. "
            "Please confirm the exact medicine names. " + "; ".join(suggestions[:3]) + "."
        )
    if unclear and matches:
        return pending_clarification_prompt(unclear, matches)
    return (
        "I could not match those medicine names to the local alias index. Please confirm the exact brand or generic "
        "names, including strength if present, separated by commas."
    )


def unclear_medication_candidates(message: str, aliases: set[str], matches: tuple[str, ...]) -> tuple[str, ...]:
    """Return medication-looking phrases from a list statement that were not matched."""
    if not medication_list_intent(message):
        return ()
    matched_norms = {normalize_lookup_text(match) for match in matches}
    unclear: list[str] = []
    seen: set[str] = set()
    for candidate in _possible_medication_phrases(message):
        normalized = normalize_lookup_text(candidate)
        if not normalized or normalized in matched_norms:
            continue
        if normalized in aliases:
            continue
        if any(normalized in match or match in normalized for match in matched_norms):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        unclear.append(candidate)
    return tuple(unclear)


def pending_clarification_prompt(unclear: tuple[str, ...], clear: tuple[str, ...] = ()) -> str:
    unclear_text = ", ".join(unclear) if unclear else "the unclear medicine"
    clear_text = f" I did recognize: {', '.join(clear)}." if clear else ""
    return (
        f"I need the exact medicine name before I can safely check {unclear_text}.{clear_text} "
        "Please type the brand/generic name exactly as written on the strip or prescription, including strength if present. "
        "For example: /add Dolo 650, ondansetron."
    )


def _possible_medication_phrases(message: str) -> tuple[str, ...]:
    pieces = re.split(r"[,;/]|\s+\band\b\s+|\s+\bwith\b\s+|\s+\balong\s+with\b\s+", message, flags=re.IGNORECASE)
    cleaned: list[str] = []
    for piece in pieces:
        piece = re.sub(
            r"\b(i|am|i'm|im|taking|take|the|a|an|tablet|tablets|capsule|capsules|medicine|medicines|meds?)\b",
            " ",
            piece,
            flags=re.IGNORECASE,
        )
        value = " ".join(re.findall(r"[A-Za-z0-9]+", piece))
        if len(value) >= 3:
            cleaned.append(value)
    return tuple(cleaned)
