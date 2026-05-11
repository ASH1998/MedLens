// Agent turn — TS port of `medlens/agent_loop.py:run_agent_turn`.
// Budgets: 6 rounds, 30 s, 4 tool calls per round, deterministic-report
// fallback when the model stalls or runs out of rounds.

import type { ChatSession, ToolCallRecord } from "../chat/session";
import { dispatch, toAnthropicTools, toGeminiTools } from "../tools/registry";
import { MedicationSafetyStore } from "../tools/safety-store";
import type { MedicationSafetyReport } from "../tools/types";
import type { AgentMessage, NativeToolProvider, ToolCall } from "../providers/types";
import { TOOL_LOOP_SYSTEM_PROMPT } from "./prompts";

export interface AgentTurnResult {
  final_text: string;
  trace: ToolCallRecord[];
  report: MedicationSafetyReport | null;
  used_tools: string[];
  provider_name: string;
  fallback_used: boolean;
}

export interface RunAgentTurnArgs {
  provider: NativeToolProvider;
  session: ChatSession;
  store: MedicationSafetyStore;
  user_message: string;
  audience_prompt?: string;
  candidate_medications?: string[];
  effect_limit?: number;
  max_rounds?: number;
  max_tool_calls_per_round?: number;
  time_budget_seconds?: number;
}

export async function runAgentTurn(args: RunAgentTurnArgs): Promise<AgentTurnResult> {
  const {
    provider,
    session,
    store,
    user_message,
    audience_prompt,
    candidate_medications = [],
    effect_limit = 5,
    max_rounds = 6,
    max_tool_calls_per_round = 4,
    time_budget_seconds = 30,
  } = args;

  session.clearTurnTrace();
  const priorMessages = textTranscript(session.transcript);
  const messages: AgentMessage[] = [
    ...priorMessages,
    {
      role: "user",
      content: userContent(user_message, candidate_medications, session.medicationInputs()),
    },
  ];
  const started = performance.now() / 1000;
  const systemPrompt = audience_prompt
    ? `${TOOL_LOOP_SYSTEM_PROMPT}\n\nAudience style:\n${audience_prompt}`
    : TOOL_LOOP_SYSTEM_PROMPT;

  let finalText = "";
  let fallbackUsed = false;

  for (let round = 0; round < max_rounds; round++) {
    if (performance.now() / 1000 - started > time_budget_seconds) {
      fallbackUsed = true;
      break;
    }
    const response = await provider.generateWithTools(
      systemPrompt,
      messages,
      toolsForProvider(provider.name),
    );
    finalText = response.text.trim();
    if (response.tool_calls.length === 0) break;

    const limited = response.tool_calls.slice(0, max_tool_calls_per_round);
    messages.push({
      role: "assistant",
      content: finalText,
      tool_calls: limited.map((c) => ({ id: c.id, name: c.name, args: c.args })),
    });
    for (const call of limited) {
      const a: Record<string, unknown> = { ...call.args };
      if (call.name === "build_structured_report" && a.limit === undefined) a.limit = effect_limit;
      const result = await dispatch(call.name, a, { store, session });
      messages.push({
        role: "tool",
        tool_call_id: call.id,
        name: call.name,
        content: result,
      });
    }
    if (round === max_rounds - 1) fallbackUsed = true;
  }

  if (fallbackUsed || !finalText) {
    const report = await store.buildStructuredReport(session.medicationInputs(), effect_limit);
    session.last_report = report;
    finalText = deterministicTextFromReport(report);
  }

  session.transcript = [
    ...priorMessages,
    { role: "user", content: user_message },
    { role: "assistant", content: finalText },
  ];

  return {
    final_text: finalText,
    trace: [...session.last_trace],
    report: session.last_report,
    used_tools: session.last_trace.map((r) => r.name),
    provider_name: provider.name,
    fallback_used: fallbackUsed,
  };
}

// ----- helpers -----

function textTranscript(messages: { role: string; content: unknown }[]): AgentMessage[] {
  return messages
    .filter(
      (m): m is AgentMessage =>
        (m.role === "user" || m.role === "assistant") &&
        !("tool_calls" in m && Array.isArray((m as AgentMessage).tool_calls)),
    )
    .slice(-12)
    .map((m) => ({ role: m.role as "user" | "assistant", content: String(m.content ?? "") }));
}

function userContent(
  message: string,
  candidates: readonly string[],
  current: readonly string[],
): string {
  const lines: string[] = [message];
  if (current.length > 0) lines.push("\nCurrent session medications: " + current.join(", "));
  if (candidates.length > 0)
    lines.push("\nCandidate medications detected by CLI args: " + candidates.join(", "));
  return lines.join("\n");
}

function deterministicTextFromReport(report: MedicationSafetyReport): string {
  const lines: string[] = [
    `I checked ${pairCountText(report.checked_pair_count)}. In my local reference set, this is marked ${report.overall_severity}.`,
  ];
  if (report.findings.length > 0) {
    for (const finding of report.findings.slice(0, 3)) {
      lines.push(...deterministicFindingLines(finding));
      lines.push("");
      lines.push("Sources:");
      const srcLines = sourceLinesFromFinding(finding);
      lines.push(...(srcLines.length > 0 ? srcLines : [`- ${finding.drug_a} + ${finding.drug_b}: no URL on file`]));
    }
    if (report.findings.length > 3) {
      lines.push("");
      lines.push(`There are ${report.findings.length - 3} more findings available. Ask for details and I can walk through them.`);
    } else {
      lines.push("");
      lines.push("Ask for details if you want the mechanism or raw signal rows.");
    }
  } else {
    lines.push(
      "I did not find a flagged interaction for these medicines in the local evidence. That does not prove the combination is safe; it only means this local reference set did not flag it.",
    );
  }
  if (report.unresolved_medications.length > 0) {
    const names = report.unresolved_medications.map((m) => m.input_name).join(", ");
    lines.push(`I could not match this locally, so I did not check it: ${names}.`);
  }
  return lines.join("\n");
}

interface FindingShape {
  drug_a: string;
  drug_b: string;
  severity: string | null;
  effects: { adverse_effect: string }[];
  source_regions: string[];
  source_bases: string[];
  source_urls: string[];
}

function deterministicFindingLines(finding: FindingShape): string[] {
  const drugA = finding.drug_a;
  const drugB = finding.drug_b;
  const severity = finding.severity ?? "flagged";
  const effects = finding.effects.slice(0, 3).map((e) => e.adverse_effect);
  const lines: string[] = [`I found a ${severity} interaction between ${drugA} and ${drugB}.`];
  if (effects.length > 0) {
    lines.push(`The main concern is ${effects.join(", ")}.`);
    const note = plainEffectNote(effects[0]);
    if (note) lines.push(note);
  }
  if (severity === "Major") {
    lines.push(
      "Because this is marked Major, it is worth asking a pharmacist or prescriber before using them together.",
    );
  } else if (effects.length > 0) {
    lines.push(
      "If you are using them together, keep an eye on those symptoms and ask a pharmacist if they show up.",
    );
  }
  return lines;
}

function sourceLinesFromFinding(finding: FindingShape): string[] {
  const regions = finding.source_regions.slice(0, 4);
  const bases = compactBasisItems(finding.source_bases, 3);
  const urls = finding.source_urls.slice(0, 20);
  if (urls.length === 0) return [];
  const meta: string[] = [];
  if (regions.length > 0) meta.push("regions: " + regions.join(", "));
  if (bases.length > 0) meta.push("basis: " + bases.join("; "));
  const metaText = meta.length > 0 ? ` (${meta.join("; ")})` : "";
  const lines = urls.slice(0, 3).map((url) => `- ${finding.drug_a} + ${finding.drug_b}: ${url}${metaText}`);
  if (urls.length > 3) {
    lines.push(
      `- ${finding.drug_a} + ${finding.drug_b}: ${urls.length - 3} more source URL(s) on file; use /sources for the full list.`,
    );
  }
  return lines;
}

function plainEffectNote(effectName: string): string {
  const lower = effectName.toLowerCase();
  if (lower.includes("gastrointestinal bleeding"))
    return "In plain language, gastrointestinal bleeding means bleeding in the stomach or intestines.";
  if (lower.includes("intracranial hemorrhage"))
    return "In plain language, intracranial hemorrhage means bleeding inside the skull.";
  if (lower.includes("qt prolongation"))
    return "In plain language, QT prolongation is an electrical heart-rhythm change that can become dangerous in some people.";
  if (lower.includes("torsades"))
    return "In plain language, torsades de pointes is a dangerous abnormal heart rhythm.";
  if (lower.includes("acute anemia"))
    return "In plain language, acute anemia means a sudden drop in red blood cells or hemoglobin.";
  return "";
}

function compactBasisItems(values: string[], limit: number): string[] {
  const out: string[] = [];
  for (const raw of values) {
    for (const piece of String(raw).split(";")) {
      const item = piece.trim();
      if (item && !out.includes(item)) out.push(item);
      if (out.length >= limit) return out;
    }
  }
  return out;
}

function pairCountText(count: number): string {
  return `${count} ${count === 1 ? "medicine pair" : "medicine pairs"}`;
}

// Re-export for tests / consumers that want to format reports without the loop.
export { deterministicTextFromReport };
export type { ToolCall };

function toolsForProvider(providerName: string): unknown {
  if (providerName === "gemini" || providerName === "google") return toGeminiTools();
  return toAnthropicTools();
}
