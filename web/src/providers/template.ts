// Offline deterministic provider — TS port of the medication-flow branches in
// `medlens/agent.py:TemplateProvider.generate_with_tools`. The full Python
// version has additional intent detectors (mechanism search, evidence sources,
// common-medicine profiles, etc.); this v1 ports the medication-list flow that
// covers the hot path: normalize → (search alias if unresolved) → add → report.
//
// Other intents fall through to the deterministic report fallback once the
// agent loop runs out of rounds, which still produces a usable answer.

import type { AgentMessage, NativeToolProvider, ToolModelResponse } from "./types";

const KNOWN_TEMPLATE_MED_TERMS = [
  "acetaminophen",
  "paracetamol",
  "ondansetron",
  "fluorouracil",
  "azithromycin",
  "ibuprofen",
  "advil",
  "warfarin",
  "dolo 650",
  "captopril",
];

const MEDICATION_STOPWORDS = new Set([
  "not",
  "should",
  "with",
  "taken",
  "take",
  "avoid",
  "med",
  "meds",
  "medicine",
  "medicines",
  "drug",
  "drugs",
  "hello",
  "hey",
  "hi",
  "there",
  "something",
  "anything",
  "wrong",
  "problem",
  "problems",
  "issue",
  "issues",
  "okay",
  "ok",
  "what",
  "about",
]);

export class TemplateProvider implements NativeToolProvider {
  readonly name = "template";

  async generateWithTools(
    _systemPrompt: string,
    messages: AgentMessage[],
    _tools: unknown,
  ): Promise<ToolModelResponse> {
    const lastUser = lastUserText(messages);
    const toolResults = collectToolResults(messages);
    const calledTools = new Set(toolResults.map((r) => r.name));

    // 1) Single-drug "what interacts with X" intent.
    const interactionDrug = interactionListDrugCandidate(lastUser);
    if (interactionDrug && !calledTools.has("list_interactions_for_drug")) {
      return tool("template-list-1", "list_interactions_for_drug", {
        drug: interactionDrug,
        limit: 12,
      });
    }
    const listResult = lastToolResult(toolResults, "list_interactions_for_drug");
    if (listResult) {
      return text(textFromInteractionList(listResult));
    }

    // 2) Medication-list flow.
    const candidates = extractMedicationCandidates(lastUser);
    if (candidates.length > 0 && !calledTools.has("normalize_medications")) {
      return tool("template-normalize-1", "normalize_medications", { names: candidates });
    }
    const normalized = lastToolResult(toolResults, "normalize_medications");
    const unresolved = unresolvedNames(normalized);
    const inferredFromAliasSearch = inferredAliasesForUnresolved(toolResults, unresolved);
    const stillUnresolved = unresolved.filter((name) => inferredFromAliasSearch[name] === undefined);
    const resolved = [...resolvedInputs(normalized), ...Object.values(inferredFromAliasSearch)];

    if (unresolved.length > 0 && !haveSearchedAll(toolResults, unresolved)) {
      return {
        text: "",
        tool_calls: unresolved.slice(0, 4).map((name, i) => ({
          id: `template-search-${i + 1}`,
          name: "search_drug_aliases",
          args: { query: name, limit: 5 },
        })),
      };
    }
    if (stillUnresolved.length > 0) {
      return text(
        `I couldn't match this locally: ${stillUnresolved.join(", ")}. Could you re-type it with the exact brand or generic name (and strength if you have it)?`,
      );
    }

    if (resolved.length > 0 && !calledTools.has("add_medications")) {
      return tool("template-add-1", "add_medications", { names: resolved });
    }

    if (resolved.length > 0 && !calledTools.has("build_structured_report")) {
      return tool("template-report-1", "build_structured_report", {});
    }

    const report = lastToolResult(toolResults, "build_structured_report");
    if (report) return text(textFromReport(report));

    return text(educationalFallbackText());
  }
}

// ----- helpers -----

function tool(id: string, name: string, args: Record<string, unknown>): ToolModelResponse {
  return { text: "", tool_calls: [{ id, name, args }] };
}
function text(t: string): ToolModelResponse {
  return { text: t, tool_calls: [] };
}

function lastUserText(messages: AgentMessage[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === "user") {
      return typeof m.content === "string" ? m.content : JSON.stringify(m.content);
    }
  }
  return "";
}

interface ToolResultEntry {
  name: string;
  content: Record<string, unknown>;
}
function collectToolResults(messages: AgentMessage[]): ToolResultEntry[] {
  return messages
    .filter((m) => m.role === "tool")
    .map((m) => ({
      name: String(m.name ?? ""),
      content: (typeof m.content === "object" && m.content !== null
        ? (m.content as Record<string, unknown>)
        : {}) as Record<string, unknown>,
    }));
}
function lastToolResult(
  results: ToolResultEntry[],
  name: string,
): Record<string, unknown> | null {
  for (let i = results.length - 1; i >= 0; i--) {
    if (results[i].name === name) return results[i].content;
  }
  return null;
}

function unresolvedNames(normalizeResult: Record<string, unknown> | null): string[] {
  if (!normalizeResult) return [];
  const list = (normalizeResult.medications ?? []) as { resolved?: boolean; input_name?: string }[];
  return list.filter((m) => m.resolved === false).map((m) => String(m.input_name ?? ""));
}
function resolvedInputs(normalizeResult: Record<string, unknown> | null): string[] {
  if (!normalizeResult) return [];
  const list = (normalizeResult.medications ?? []) as { resolved?: boolean; input_name?: string }[];
  return list.filter((m) => m.resolved === true).map((m) => String(m.input_name ?? ""));
}
function haveSearchedAll(results: ToolResultEntry[], unresolved: string[]): boolean {
  const searched = new Set(
    results
      .filter((r) => r.name === "search_drug_aliases")
      .map((r) => String((r.content as { query?: string }).query ?? "").toLowerCase()),
  );
  return unresolved.every((n) => searched.has(n.toLowerCase()));
}

function inferredAliasesForUnresolved(
  results: ToolResultEntry[],
  unresolved: readonly string[],
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const name of unresolved) {
    const search = lastSearchResultForQuery(results, name);
    const matches = (search?.matches ?? []) as { canonical?: string; aliases?: string[] }[];
    const first = matches[0];
    const alias = first?.aliases?.[0] ?? first?.canonical;
    if (alias) out[name] = alias;
  }
  return out;
}

function lastSearchResultForQuery(
  results: ToolResultEntry[],
  query: string,
): Record<string, unknown> | null {
  const wanted = query.toLowerCase();
  for (let i = results.length - 1; i >= 0; i--) {
    const item = results[i];
    if (item.name !== "search_drug_aliases") continue;
    if (String(item.content.query ?? "").toLowerCase() === wanted) return item.content;
  }
  return null;
}

function interactionListDrugCandidate(text: string): string {
  const firstLine = text.split("\n")[0] ?? text;
  const lower = firstLine.toLowerCase();
  const intentTerms = [
    "what medicines",
    "what meds",
    "which medicines",
    "which meds",
    "what drugs",
    "which drugs",
    "should not be taken",
    "shouldn't be taken",
    "can't be taken",
    "cant be taken",
    "cannot be taken",
    "not be taken",
    "not taken",
    "avoid with",
    "should i avoid",
    "interact with",
    "interacts with",
    "interactions with",
  ];
  if (!intentTerms.some((t) => lower.includes(t))) return "";
  for (const pattern of [
    /\b(?:with|against|for)\s+([A-Za-z][A-Za-z0-9 -]{1,60})/i,
    /\b(?:avoid|taking|taken)\s+([A-Za-z][A-Za-z0-9 -]{1,60})/i,
  ]) {
    const m = firstLine.match(pattern);
    if (m) {
      const candidate = cleanMedicationCandidate(m[1].split(/[.?!,;]/)[0] ?? "");
      if (candidate && candidate.length >= 3) return candidate;
    }
  }
  const known = knownTermsInText(firstLine);
  return known[0] ?? "";
}

function knownTermsInText(value: string): string[] {
  const lower = value.toLowerCase();
  return KNOWN_TEMPLATE_MED_TERMS.filter((term) =>
    new RegExp(`\\b${term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i").test(lower),
  );
}

function extractMedicationCandidates(userText: string): string[] {
  const firstLine = userText.split("\n")[0] ?? userText;
  const lower = firstLine.toLowerCase();
  const knownInLine = knownTermsInText(firstLine);
  if (knownInLine.length >= 2) return knownInLine;
  if (
    !firstLine.includes(",") &&
    !["take", "taking", "med", "medicine", "along with", "with", "what about"].some((t) =>
      lower.includes(t),
    )
  ) {
    return [];
  }
  const stripped = firstLine.replace(
    /\b(i|am|i'm|im|what|which|cant|can't|cannot|be|taking|taken|take|avoid|interact|interacts|interactions|can|could|should|is|are|ok|okay|safe|together|the|a|an|tablet|tablets|capsule|capsules|drug|drugs|medicine|medicines|meds?|my)\b/gi,
    " ",
  );
  const pieces = stripped.split(/[,;/]|\s+\band\b\s+|\s+\bwith\b\s+|\s+\balong\s+with\b\s+/gi);
  const candidates: string[] = [];
  for (const piece of pieces) {
    const head = (piece.split(/[.?!]/)[0] ?? "").trim();
    const value = (head.match(/[A-Za-z0-9]+/g) ?? []).join(" ").trim();
    const known = knownTermsInText(value);
    if (known.length > 0) {
      candidates.push(...known);
      continue;
    }
    const cleaned = cleanMedicationCandidate(value);
    if (cleaned.length >= 3) candidates.push(cleaned);
  }
  if (candidates.length === 1) {
    const known = knownTermsInText(candidates[0]);
    if (known.length >= 2) return known;
  }
  return candidates;
}

function cleanMedicationCandidate(value: string): string {
  const words = value
    .match(/[A-Za-z0-9]+/g)
    ?.map((word) => word.toLowerCase())
    .filter((word) => !MEDICATION_STOPWORDS.has(word));
  return (words ?? []).join(" ").trim();
}

function textFromReport(report: Record<string, unknown>): string {
  const overall = String(report.overall_severity ?? "None");
  const findings = (report.findings ?? []) as {
    drug_a: string;
    drug_b: string;
    severity: string;
    effects?: { adverse_effect: string }[];
    source_urls?: string[];
  }[];
  const unresolved = (report.unresolved_medications ?? []) as { input_name: string }[];
  const lines = [`Overall local evidence severity: ${overall}.`];
  if (findings.length > 0) {
    lines.push("Here is what I would pay attention to:");
    for (const f of findings) {
      const effects = (f.effects ?? [])
        .slice(0, 3)
        .map((e) => e.adverse_effect)
        .join(", ");
      const suffix = effects ? ` The main things to watch for are ${effects}.` : "";
      lines.push(`- ${f.drug_a} + ${f.drug_b} is a ${f.severity} finding.${suffix}`);
    }
    const urls = findings.flatMap((f) => f.source_urls ?? []).slice(0, 6);
    if (urls.length > 0) {
      lines.push("");
      lines.push("Sources:");
      for (const u of urls) lines.push(`- ${u}`);
    }
    lines.push("For a Major finding, it is worth asking a pharmacist or prescriber before combining these.");
  } else {
    lines.push("I did not find a flagged interaction for these medicines in the local evidence.");
  }
  if (unresolved.length > 0) {
    const names = unresolved.map((u) => u.input_name).join(", ");
    lines.push(`I couldn't match these locally, so I did not check them: ${names}.`);
  }
  return lines.join("\n");
}

function textFromInteractionList(result: Record<string, unknown>): string {
  const drug = (result.drug ?? {}) as { canonical_name?: string; input_name?: string };
  const interactions = (result.interactions ?? []) as {
    partner: string;
    severity: string;
    top_effects?: { adverse_effect: string }[];
  }[];
  const name = drug.canonical_name ?? drug.input_name ?? "this medicine";
  if (interactions.length === 0) {
    return `I do not have a locally flagged interaction list for ${name} in the current evidence.`;
  }
  const lines = [
    `Local reference list of medicines that have a flagged interaction with ${name} (not a universal do-not-take list):`,
  ];
  for (const i of interactions.slice(0, 12)) {
    const effects = (i.top_effects ?? [])
      .slice(0, 2)
      .map((e) => e.adverse_effect)
      .join(", ");
    const suffix = effects ? ` — watch for ${effects}` : "";
    lines.push(`- ${i.partner} (${i.severity})${suffix}`);
  }
  return lines.join("\n");
}

function educationalFallbackText(): string {
  return [
    "I focus on local medication-interaction checks against the curated DDI evidence.",
    "Tell me which medicines you are taking (brand or generic) and I'll check pairs against the local reference set.",
  ].join(" ");
}
