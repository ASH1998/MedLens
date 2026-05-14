"""Native tool-calling loop over deterministic MedLens SQLite tools."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from medlens.agent import AGENT_SYSTEM_PROMPT, ToolModelResponse
from medlens.chat.session import ChatSession, ToolCallRecord
from medlens.tools.local_safety import MedicationSafetyReport, MedicationSafetyStore
from medlens.tools.registry import dispatch, to_bedrock_tools, to_gemini_tools


TOOL_LOOP_SYSTEM_PROMPT = (
    AGENT_SYSTEM_PROMPT
    + """

How to use the local SQLite tools:
- You are a real agent. Run the tools - don't guess. The tools are the source of truth for every factual claim about a medication, pair, severity, effect, mechanism, region, or source URL.
- When the user lists medications, call normalize_medications first with their exact wording.
- For names that don't normalize, call search_drug_aliases on each unresolved string. If the alias search returns a confident match, use it - you don't always need to ask the user. If the user is asking what a brand/common medicine is, or asks about use, strength, formulation, OTC/Rx status, India relevance, or local risk flags, call get_common_medicine_profile or search_common_medicines from normalization.sqlite.
- Once you have clean names, call add_medications, then build_structured_report. Read the report carefully and report the real findings (top effects, mechanism, regions, source_basis, source_urls) in your reply.
- When the user asks broad anchored questions like "what medicines interact with X" or "what can't be taken with X", call list_interactions_for_drug. Use its min_severity, region, and risk_flag filters when the user asks for major-only, India/US/EU-specific, kidney/pregnancy/liver-risk, or similar filtered lists. Make clear that this is a locally flagged interaction list, not a universal do-not-take list.
- When the user asks global DDI questions that are not anchored to their current medication list - for example "what interactions cause hyperkalemia?", "show major India interactions", or "find bleeding interactions" - call search_interactions with the relevant effect, min_severity, region, risk_flag, or drug filter.
- When the user asks about a mechanism/category text such as CYP inhibition, QT prolongation mechanism, additive bleeding, renal perfusion, or similar source wording, call search_interactions_by_mechanism. Treat mechanism results as noisy source-text hints, not a clean ontology.
- When the user asks whether one or more new candidate medicines are safe to add against the current medication list, call bulk_check_pairs. Use an explicit against list if the user provides one; otherwise use the current session medications.
- For a single specific pair the user asks about, lookup_pair, get_pair_effects, severity_consensus, and get_raw_signals are the right tools. Use get_full_raw_signals when the user asks to audit, debug, inspect raw rows, see source rows, or understand exactly where a claim came from.
- Pull mechanism and source URLs from the raw signals when they're available - that's exactly the kind of detail that makes the answer feel like an expert pharmacist instead of a checklist.
- When the user asks "what brands/aliases map to this drug?" call list_aliases_for_drug. When the user asks to browse drug categories or list drugs in a category, call list_drugs_by_category.
- For dataset/artifact questions, call list_evidence_sources to summarize source-file coverage. Call list_import_issues when the user asks what failed to import, what normalization gaps remain, or why row counts are unresolved.
- Always choose the database-backed tool that matches the question: normalization.sqlite for aliases, OCR recovery, brand/common medicine profiles, and common medicine search; evidence.sqlite for DDI pairs, effects, raw signals, evidence sources, and import issues.

Length and depth:
- Match the user's question. If they listed two drugs and walked away, a focused 4-8 sentence answer with the real interaction, why it matters, and what they should know is right.
- Use a compact bullet list ONLY when it genuinely helps (e.g., several distinct effects). Otherwise prose flows better.
- Don't truncate to "top 3" or pad with disclaimers - cover what's clinically relevant from the tool output.
- For Major findings, mention talking to their prescriber or pharmacist once, naturally. For Moderate/Minor, describe what to watch for. For no findings, just say there's no flagged interaction in the local evidence and stop.
- If build_structured_report returns duplicate_ingredient_warnings, lead with those dose-limit concerns before pairwise severity.
- If a finding includes practical_guidance, use it to distinguish the reference severity from practical day-to-day interpretation. Do not soften critical interactions unless practical_guidance supports it.

Sources are mandatory:
- After your explanation, add a short "Sources" section. List every source_urls entry from the tool result for each pair you discussed (one per line). Include source_regions and source_bases on the same lines when they're present in the tool result.
- Never silently drop URLs that the tool returned. If a URL is in the tool output, it must appear in your answer.

When to ask, when to act:
- If a name is genuinely ambiguous (multiple plausible matches with no clear winner, or a totally unrecognized brand), ask one focused clarification question.
- If alias search gives a confident match, just go.
- For symptom/diagnosis questions (not interaction questions), say briefly that you focus on medication interactions and suggest the right next step (pharmacist, clinician, urgent care for red-flag symptoms). Don't try to diagnose with the medication tools.
"""
)


class NativeToolProvider(Protocol):
    name: str

    def generate_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ToolModelResponse:
        """Return assistant text and optional native tool calls."""


@dataclass(frozen=True)
class AgentTurnResult:
    final_text: str
    trace: tuple[ToolCallRecord, ...]
    report: MedicationSafetyReport | None
    used_tools: tuple[str, ...]
    provider_name: str
    fallback_used: bool = False

    def to_debug_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider_name,
            "fallback_used": self.fallback_used,
            "used_tools": list(self.used_tools),
            "trace": [
                {
                    "name": record.name,
                    "args": record.args,
                    "result": record.result,
                    "error": record.error,
                    "duration_ms": record.duration_ms,
                }
                for record in self.trace
            ],
            "report": self.report.to_dict() if self.report is not None else None,
            "final_text": self.final_text,
        }


def run_agent_turn(
    *,
    provider: object,
    session: ChatSession,
    store: MedicationSafetyStore,
    user_message: str,
    candidate_medications: tuple[str, ...] = (),
    effect_limit: int = 5,
    max_rounds: int = 6,
    max_tool_calls_per_round: int = 4,
    time_budget_seconds: float = 30.0,
) -> AgentTurnResult:
    """Run one native tool-calling turn and dispatch tool calls through SQLite."""
    session.clear_turn_trace()
    prior_messages = _text_transcript(session.transcript)
    messages = list(prior_messages)
    messages.append(
        {
            "role": "user",
            "content": _user_content(
                user_message,
                candidate_medications,
                current_medications=session.medication_inputs(),
            ),
        }
    )
    started = time.monotonic()

    if not hasattr(provider, "generate_with_tools"):
        return _fallback_turn(provider=provider, session=session, store=store, messages=messages, effect_limit=effect_limit)

    tools = _tools_for_provider(str(getattr(provider, "name", "")))
    final_text = ""
    fallback_used = False

    for _round in range(max_rounds):
        if time.monotonic() - started > time_budget_seconds:
            fallback_used = True
            break
        response = provider.generate_with_tools(TOOL_LOOP_SYSTEM_PROMPT, messages, tools)  # type: ignore[attr-defined]
        final_text = response.text.strip()
        if not response.tool_calls:
            break

        limited_calls = response.tool_calls[:max_tool_calls_per_round]
        messages.append(
            {
                "role": "assistant",
                "content": final_text,
                "tool_calls": [
                    {"id": call.id, "name": call.name, "args": call.args}
                    for call in limited_calls
                ],
            }
        )
        for call in limited_calls:
            args = dict(call.args)
            if call.name == "build_structured_report":
                args.setdefault("limit", effect_limit)
            result = dispatch(call.name, args, store=store, session=session)
            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": result})
    else:
        fallback_used = True

    if fallback_used or not final_text:
        report = store.build_structured_report(session.medication_inputs(), effect_limit=effect_limit)
        session.last_report = report
        final_text = _deterministic_text_from_report(report)

    session.transcript = prior_messages + [{"role": "user", "content": user_message}, {"role": "assistant", "content": final_text}]
    return AgentTurnResult(
        final_text=final_text,
        trace=tuple(session.last_trace),
        report=session.last_report,
        used_tools=tuple(record.name for record in session.last_trace),
        provider_name=str(getattr(provider, "name", "unknown")),
        fallback_used=fallback_used,
    )


def _fallback_turn(
    *,
    provider: object,
    session: ChatSession,
    store: MedicationSafetyStore,
    messages: list[dict[str, object]],
    effect_limit: int,
) -> AgentTurnResult:
    report = store.build_structured_report(session.medication_inputs(), effect_limit=effect_limit)
    session.last_report = report
    text = _deterministic_text_from_report(report)
    session.transcript = _text_transcript(session.transcript) + [{"role": "user", "content": str(messages[-1].get("content", ""))}, {"role": "assistant", "content": text}]
    return AgentTurnResult(
        final_text=text,
        trace=tuple(session.last_trace),
        report=report,
        used_tools=(),
        provider_name=str(getattr(provider, "name", "unknown")),
        fallback_used=True,
    )


def _tools_for_provider(provider_name: str) -> list[dict[str, object]]:
    if provider_name in {"bedrock", "aws", "claude"}:
        return to_bedrock_tools()
    if provider_name in {"gemini", "google"}:
        return to_gemini_tools()
    return to_bedrock_tools()


def _user_content(
    message: str,
    candidate_medications: tuple[str, ...],
    current_medications: tuple[str, ...] = (),
) -> str:
    lines = [message]
    if current_medications:
        lines.append("\nCurrent session medications: " + ", ".join(current_medications))
    if candidate_medications:
        lines.append("\nCandidate medications detected by CLI args: " + ", ".join(candidate_medications))
    return "\n".join(lines)


def _text_transcript(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {"role": str(message.get("role")), "content": str(message.get("content", ""))}
        for message in messages
        if message.get("role") in {"user", "assistant"} and "tool_calls" not in message
    ][-12:]


def _deterministic_text_from_report(report: MedicationSafetyReport) -> str:
    lines = [
        f"I checked {_pair_count_text(report.checked_pair_count)}. In my local reference set, this is marked {report.overall_severity}.",
    ]
    if report.findings:
        for finding in report.findings[:3]:
            lines.extend(_deterministic_finding_lines(finding))
            lines.append("")
            lines.append("Sources:")
            source_lines = _source_lines_from_finding(finding)
            lines.extend(source_lines or [f"- {finding.drug_a} + {finding.drug_b}: no URL on file"])
        if len(report.findings) > 3:
            lines.append("")
            lines.append(f"There are {len(report.findings) - 3} more findings available. Ask for details and I can walk through them.")
        else:
            lines.append("")
            lines.append("Ask for details if you want the mechanism or raw signal rows.")
    else:
        lines.append("I did not find a flagged interaction for these medicines in the local evidence. That does not prove the combination is safe; it only means this local reference set did not flag it.")
    if report.unresolved_medications:
        names = ", ".join(item.input_name for item in report.unresolved_medications)
        lines.append(f"I could not match this locally, so I did not check it: {names}.")
    return "\n".join(lines)


def _deterministic_finding_lines(finding: object) -> list[str]:
    drug_a = str(getattr(finding, "drug_a", "medicine A"))
    drug_b = str(getattr(finding, "drug_b", "medicine B"))
    severity = str(getattr(finding, "severity", "flagged"))
    effects = [effect.adverse_effect for effect in getattr(finding, "effects", ())[:3]]

    lines = [f"I found a {severity} interaction between {drug_a} and {drug_b}."]
    if effects:
        lines.append(f"The main concern is {', '.join(effects)}.")
        plain_note = _plain_effect_note(effects[0])
        if plain_note:
            lines.append(plain_note)
    if severity == "Major":
        lines.append("Because this is marked Major, it is worth asking a pharmacist or prescriber before using them together.")
    elif effects:
        lines.append("If you are using them together, keep an eye on those symptoms and ask a pharmacist if they show up.")
    return lines


def _source_lines_from_finding(finding: object) -> list[str]:
    drug_a = str(getattr(finding, "drug_a", "medicine A"))
    drug_b = str(getattr(finding, "drug_b", "medicine B"))
    regions = list(getattr(finding, "source_regions", ())[:4])
    bases = _compact_basis_items(getattr(finding, "source_bases", ()), 3)
    urls = list(getattr(finding, "source_urls", ())[:20])
    if not urls:
        return []
    meta_parts: list[str] = []
    if regions:
        meta_parts.append("regions: " + ", ".join(regions))
    if bases:
        meta_parts.append("basis: " + "; ".join(bases))
    meta = f" ({'; '.join(meta_parts)})" if meta_parts else ""
    lines = [f"- {drug_a} + {drug_b}: {url}{meta}" for url in urls[:3]]
    if len(urls) > 3:
        lines.append(f"- {drug_a} + {drug_b}: {len(urls) - 3} more source URL(s) on file; use /sources for the full list.")
    return lines


def _plain_effect_note(effect_name: str) -> str:
    normalized = effect_name.casefold()
    if "gastrointestinal bleeding" in normalized:
        return "In plain language, gastrointestinal bleeding means bleeding in the stomach or intestines."
    if "intracranial hemorrhage" in normalized:
        return "In plain language, intracranial hemorrhage means bleeding inside the skull."
    if "qt prolongation" in normalized:
        return "In plain language, QT prolongation is an electrical heart-rhythm change that can become dangerous in some people."
    if "torsades" in normalized:
        return "In plain language, torsades de pointes is a dangerous abnormal heart rhythm."
    if "acute anemia" in normalized:
        return "In plain language, acute anemia means a sudden drop in red blood cells or hemoglobin."
    return ""


def _compact_basis_items(value: object, limit: int) -> list[str]:
    items: list[str] = []
    for raw in value if isinstance(value, tuple) else ():
        for piece in str(raw).split(";"):
            item = piece.strip()
            if item and item not in items:
                items.append(item)
            if len(items) >= limit:
                return items
    return items


def _pair_count_text(count: int) -> str:
    noun = "medicine pair" if count == 1 else "medicine pairs"
    return f"{count} {noun}"
