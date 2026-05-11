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
  "if",
  "it",
  "its",
  "this",
  "that",
  "them",
  "tak",
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

    // 2) Common India medicine profile/search flow.
    const profileNames = commonProfileCandidates(lastUser);
    if (profileNames.length > 0 && !calledCommonProfiles(toolResults, profileNames)) {
      return {
        text: "",
        tool_calls: profileNames.slice(0, 3).map((name, i) => ({
          id: `template-common-profile-${i + 1}`,
          name: "get_common_medicine_profile",
          args: { name, limit: 3 },
        })),
      };
    }
    const profileResults = allToolResults(toolResults, "get_common_medicine_profile");
    if (profileResults.length > 0) return text(textFromCommonProfiles(profileResults));

    const commonSearch = commonSearchQuery(lastUser);
    if (commonSearch && !calledTools.has("search_common_medicines")) {
      return tool("template-common-search-1", "search_common_medicines", {
        query: commonSearch,
        limit: 8,
      });
    }
    const commonSearchResult = lastToolResult(toolResults, "search_common_medicines");
    if (commonSearchResult) return text(textFromCommonSearch(commonSearchResult));

    // 3) Medication-list flow.
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
      return text(clarificationFromUnresolved(stillUnresolved, resolved, toolResults));
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

function allToolResults(results: ToolResultEntry[], name: string): Record<string, unknown>[] {
  return results.filter((r) => r.name === name).map((r) => r.content);
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
    if (first?.canonical) out[name] = first.canonical;
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

function clarificationFromUnresolved(
  unresolved: readonly string[],
  resolved: readonly string[],
  results: readonly ToolResultEntry[],
): string {
  const suggestions: string[] = [];
  for (const name of unresolved) {
    const search = lastSearchResultForQuery([...results], name);
    const matches = (search?.matches ?? []) as { canonical?: string }[];
    for (const match of matches.slice(0, 2)) {
      if (match.canonical && !suggestions.includes(match.canonical)) suggestions.push(match.canonical);
    }
  }
  const recognized = resolved.length > 0 ? ` I recognized: ${resolved.join(", ")}.` : "";
  const possible =
    suggestions.length > 0 ? ` Possible local matches include: ${suggestions.slice(0, 3).join(", ")}.` : "";
  return `I could not confidently match ${unresolved.join(", ")}.${recognized}${possible} Please confirm the exact brand/generic name and strength before I check interactions.`;
}

function calledCommonProfiles(results: ToolResultEntry[], names: readonly string[]): boolean {
  return results.filter((r) => r.name === "get_common_medicine_profile").length >= Math.min(names.length, 3);
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

function commonProfileCandidates(userText: string): string[] {
  const firstLine = userText.split("\n")[0] ?? userText;
  const lower = firstLine.toLowerCase();
  const profileTerms = [
    "what is",
    "what are",
    "what's",
    "whats",
    "profile",
    "composition",
    "strength",
    "dosage",
    "brand",
    "otc",
    "rx",
    "used for",
    "use of",
  ];
  if (!profileTerms.some((term) => lower.includes(term))) return [];

  const candidates = extractMedicationCandidates(userText);
  if (candidates.length > 0) return Array.from(new Set(candidates));

  for (const pattern of [
    /\b(?:what is|what are|what's|whats|profile for|about|composition of|strength of|use of)\s+([A-Za-z][A-Za-z0-9 +/.-]{1,80})/i,
    /\b(?:is|are)\s+([A-Za-z][A-Za-z0-9 +/.-]{1,80})\s+(?:otc|rx|prescription|used)/i,
  ]) {
    const match = firstLine.match(pattern);
    if (!match) continue;
    let candidate = (match[1].split(/[.?!;]/)[0] ?? "").trim();
    candidate = candidate.replace(/\b(used for|useful for|medicine|tablet|capsule|brand|generic)\b.*$/i, " ");
    const value = (candidate.match(/[A-Za-z0-9]+/g) ?? []).join(" ").trim();
    if (value.length >= 3 && !["these", "this medicine", "this"].includes(value.toLowerCase())) {
      return [value];
    }
  }
  return [];
}

function commonSearchQuery(userText: string): string {
  const firstLine = userText.split("\n")[0] ?? userText;
  const lower = firstLine.toLowerCase();
  if (
    !["common medicines for", "medicines for", "medicine for", "drugs for", "search common"].some(
      (term) => lower.includes(term),
    )
  ) {
    return "";
  }
  const match = firstLine.match(/\b(?:for|search common)\s+([A-Za-z][A-Za-z0-9 -]{1,80})/i);
  if (!match) return "";
  const candidate = match[1].split(/[.?!,;]/)[0] ?? "";
  return (candidate.match(/[A-Za-z0-9]+/g) ?? []).join(" ").trim();
}

function textFromReport(report: Record<string, unknown>): string {
  const findings = (report.findings ?? []) as {
    drug_a: string;
    drug_b: string;
    severity: string;
    effects?: { adverse_effect: string }[];
    source_regions?: string[];
    source_bases?: string[];
    source_urls?: string[];
  }[];
  const unresolved = (report.unresolved_medications ?? []) as { input_name: string }[];
  const checkedPairCount = Number(report.checked_pair_count ?? 0);
  const overall = String(report.overall_severity ?? "None");
  const lines = [`I checked ${pairCountText(checkedPairCount)}. In my local reference set, this is marked ${overall}.`];
  if (findings.length > 0) {
    const first = findings[0];
    lines.push(...findingExplanationLines(first));
    const srcLines = sourceLinesFromFinding(first);
    lines.push("");
    lines.push("Sources:");
    if (srcLines.length > 0) {
      lines.push(...srcLines);
    } else {
      lines.push(`- ${first.drug_a} + ${first.drug_b}: no URL on file`);
    }
    if (findings.length > 3) {
      lines.push("");
      lines.push(`There are ${findings.length - 3} more findings available. Ask for details and I can walk through them.`);
    } else if (findings.length > 1) {
      lines.push("");
      lines.push(`There are ${findings.length - 1} more findings available. Ask for details and I can walk through them.`);
    }
  } else {
    lines.push(
      "I did not find a flagged interaction for these medicines in the local evidence. That does not prove the combination is safe; it only means this local reference set did not flag it.",
    );
    lines.push("If you have symptoms or a condition that changes risk, a pharmacist can help interpret it.");
  }
  if (unresolved.length > 0) {
    const names = unresolved.map((u) => u.input_name).join(", ");
    lines.push(`I could not match this locally, so I did not check it: ${names}.`);
  }
  return lines.join("\n");
}

function textFromCommonProfiles(results: readonly Record<string, unknown>[]): string {
  const lines: string[] = [];
  for (const result of results) {
    const normalized = (result.normalized ?? {}) as {
      canonical_name?: string | null;
      input_name?: string;
      input?: string;
    };
    const canonical = String(
      normalized.canonical_name ?? normalized.input_name ?? normalized.input ?? "",
    );
    const matches = (result.matches ?? []) as {
      canonical_name?: string;
      generic_or_common_name?: string;
      common_daily_life_use_india?: string;
      dosage_form?: string;
      composition_or_strength_pattern?: string;
      common_brand_examples_india?: string;
      otc_or_rx?: string;
      patient_risk_flags_india?: string;
      source_urls?: string;
    }[];
    if (matches.length === 0) {
      const query = String(result.query ?? (canonical || "that medicine"));
      lines.push(`I could not find an India common-medicine profile for ${query} in the local normalization database.`);
      continue;
    }
    const first = matches[0];
    const name = canonical || first.canonical_name || first.generic_or_common_name || "this medicine";
    const common = first.generic_or_common_name || name;
    lines.push(`${common} maps locally to ${name}.`);
    if (first.common_daily_life_use_india) {
      lines.push(`Common India use context: ${first.common_daily_life_use_india}.`);
    }
    const formStrength = [first.dosage_form, first.composition_or_strength_pattern].filter(Boolean);
    if (formStrength.length > 0) {
      lines.push(`Forms/strengths in the local catalogue: ${formStrength.join(", ")}.`);
    }
    if (first.common_brand_examples_india) {
      lines.push(`Brand examples in the dataset: ${first.common_brand_examples_india}.`);
    }
    if (first.otc_or_rx) {
      lines.push(`Availability context: ${first.otc_or_rx}.`);
    }
    if (first.patient_risk_flags_india) {
      lines.push(`Risk flags to notice: ${first.patient_risk_flags_india}.`);
    }
    if (first.source_urls) {
      lines.push(`Sources: ${first.source_urls}`);
    }
    if (results.length > 1) lines.push("");
  }
  return lines.join("\n").trim();
}

function textFromCommonSearch(result: Record<string, unknown>): string {
  const query = String(result.query ?? "that search");
  const matches = (result.matches ?? []) as {
    generic_or_common_name?: string;
    canonical_name?: string;
    therapeutic_category?: string;
    common_daily_life_use_india?: string;
    common_brand_examples_india?: string;
  }[];
  if (matches.length === 0) return `I did not find common India medicine catalogue matches for ${query}.`;
  const lines = [`I found ${matches.length} common India medicine catalogue match(es) for ${query}:`];
  for (const item of matches.slice(0, 8)) {
    const name = item.generic_or_common_name || item.canonical_name || "medicine";
    const detail = [
      item.therapeutic_category,
      item.common_daily_life_use_india,
      item.common_brand_examples_india ? `brands: ${item.common_brand_examples_india}` : "",
    ].filter(Boolean);
    lines.push(`- ${name}: ${detail.join("; ")}`);
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
  const shown = interactions.slice(0, 8);
  const lines = [
    `I am showing ${interactions.length} locally flagged medicine(s) for ${name}.`,
    "This is not a universal do-not-take list; it is a list of combinations worth checking with a pharmacist or prescriber.",
    "",
    "Highest-priority matches:",
  ];
  for (const i of shown) {
    const effects = (i.top_effects ?? [])
      .slice(0, 2)
      .map((e) => e.adverse_effect)
      .join(", ");
    const suffix = effects ? ` Main concern: ${effects}.` : "";
    lines.push(`- ${i.partner}: ${i.severity}.${suffix}`);
  }
  if (interactions.length > shown.length) {
    lines.push(`- ${interactions.length - shown.length} more locally flagged match(es) available.`);
  }
  return lines.join("\n");
}

function educationalFallbackText(): string {
  return [
    "I can help with medication interaction questions, but I am not the right tool for diagnosing symptoms or choosing a treatment.",
    "A pharmacist or clinician can help connect symptoms with your own health history and medicines. If there is chest pain, trouble breathing, heavy bleeding, sudden weakness, or severe allergic symptoms, seek urgent care.",
  ].join(" ");
}

function findingExplanationLines(finding: {
  drug_a: string;
  drug_b: string;
  severity: string;
  effects?: { adverse_effect: string }[];
}): string[] {
  const effects = (finding.effects ?? []).slice(0, 3).map((effect) => effect.adverse_effect);
  const lines = [`I found a ${finding.severity || "flagged"} interaction between ${finding.drug_a} and ${finding.drug_b}.`];
  if (effects.length > 0) {
    lines.push(`The main concern is ${effects.join(", ")}.`);
    const note = plainEffectNote(effects[0]);
    if (note) lines.push(note);
  }
  if (finding.severity === "Major") {
    lines.push("Because this is marked Major, it is worth asking a pharmacist or prescriber before using them together.");
  } else if (effects.length > 0) {
    lines.push("If you are using them together, keep an eye on those symptoms and ask a pharmacist if they show up.");
  }
  return lines;
}

function sourceLinesFromFinding(finding: {
  drug_a: string;
  drug_b: string;
  source_regions?: string[];
  source_bases?: string[];
  source_urls?: string[];
}): string[] {
  const regions = (finding.source_regions ?? []).slice(0, 4);
  const bases = compactBasisItems(finding.source_bases ?? [], 3);
  const urls = (finding.source_urls ?? []).slice(0, 20);
  if (urls.length === 0) return [];
  const meta: string[] = [];
  if (regions.length > 0) meta.push("regions: " + regions.join(", "));
  if (bases.length > 0) meta.push("basis: " + bases.join("; "));
  const metaText = meta.length > 0 ? ` (${meta.join("; ")})` : "";
  const lines = urls.slice(0, 3).map((url) => `- ${finding.drug_a} + ${finding.drug_b}: ${url}${metaText}`);
  if (urls.length > 3) {
    lines.push(`- ${finding.drug_a} + ${finding.drug_b}: ${urls.length - 3} more source URL(s) on file; use /sources for the full list.`);
  }
  return lines;
}

function plainEffectNote(effectName: string): string {
  const normalized = effectName.toLowerCase();
  if (normalized.includes("gastrointestinal bleeding"))
    return "In plain language, gastrointestinal bleeding means bleeding in the stomach or intestines.";
  if (normalized.includes("intracranial hemorrhage"))
    return "In plain language, intracranial hemorrhage means bleeding inside the skull.";
  if (normalized.includes("qt prolongation"))
    return "In plain language, QT prolongation is an electrical heart-rhythm change that can become dangerous in some people.";
  if (normalized.includes("torsades"))
    return "In plain language, torsades de pointes is a dangerous abnormal heart rhythm.";
  if (normalized.includes("acute anemia"))
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
