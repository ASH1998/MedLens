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
- For names that don't normalize, call search_drug_aliases on each unresolved string. If the alias search returns a confident match, use it - you don't always need to ask the user.
- Once you have clean names, call add_medications, then build_structured_report. Read the report carefully and report the real findings (top effects, mechanism, regions, source_basis, source_urls) in your reply.
- For a single specific pair the user asks about, lookup_pair, get_pair_effects, severity_consensus, and get_raw_signals are the right tools. Use them.
- Pull mechanism and source URLs from the raw signals when they're available - that's exactly the kind of detail that makes the answer feel like an expert pharmacist instead of a checklist.

Length and depth:
- Match the user's question. If they listed two drugs and walked away, a focused 4-8 sentence answer with the real interaction, why it matters, and what they should know is right.
- Use a compact bullet list ONLY when it genuinely helps (e.g., several distinct effects). Otherwise prose flows better.
- Don't truncate to "top 3" or pad with disclaimers - cover what's clinically relevant from the tool output.
- For Major findings, mention talking to their prescriber or pharmacist once, naturally. For Moderate/Minor, describe what to watch for. For no findings, just say there's no flagged interaction in the local evidence and stop.

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
        f"Checked {report.checked_pair_count} pair(s) in the local DDI evidence. Overall severity: {report.overall_severity}.",
    ]
    if report.findings:
        for finding in report.findings[:3]:
            effects = ", ".join(effect.adverse_effect for effect in finding.effects[:2])
            suffix = f" - watch for {effects}" if effects else ""
            regions = f", regions: {', '.join(finding.source_regions[:4])}" if finding.source_regions else ""
            source_line = _source_line_from_finding(finding)
            source_suffix = f" Source: {source_line}." if source_line else ""
            lines.append(f"- {finding.drug_a} + {finding.drug_b} ({finding.severity}, {finding.row_count} rows{regions}){suffix}.{source_suffix}")
        if len(report.findings) > 3:
            lines.append(f"- {len(report.findings) - 3} more findings available. Ask for details to see them all.")
        else:
            lines.append("Ask for details if you want the mechanism, raw signal rows, or what to bring up with your prescriber.")
    else:
        lines.append("No flagged interaction in the local DDI evidence for these pairs.")
    if report.unresolved_medications:
        names = ", ".join(item.input_name for item in report.unresolved_medications)
        lines.append(f"Couldn't match locally, so I didn't check: {names}.")
    return "\n".join(lines)


def _source_line_from_finding(finding: object) -> str:
    regions = list(getattr(finding, "source_regions", ())[:4])
    bases = list(getattr(finding, "source_bases", ())[:3])
    urls = list(getattr(finding, "source_urls", ())[:4])
    parts: list[str] = []
    if regions:
        parts.append("regions: " + ", ".join(regions))
    if bases:
        parts.append("basis: " + "; ".join(bases))
    if urls:
        parts.append("urls: " + " | ".join(urls))
    return "; ".join(parts)
