// Provider-neutral tool registry — verbatim port of `medlens/tools/registry.py`.
// 25 tool schemas (counted 2026-05-07 against the Python source) plus a JSON-safe
// dispatcher that records each call into ChatSession.last_trace.

import type { ChatSession, ToolCallRecord } from "../chat/session";
import { normalizeLookupText } from "../db/normalize";
import { MedicationSafetyStore } from "./safety-store";
import type {
  InteractionEffect,
  KnownInteraction,
  NormalizedMedication,
  RawDdiSignal,
} from "./types";

export const TOOL_SCHEMAS: ReadonlyArray<{
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}> = [
  {
    name: "add_medications",
    description: "Add medication names to the current chat session.",
    input_schema: {
      type: "object",
      properties: { names: { type: "array", items: { type: "string" } } },
      required: ["names"],
    },
  },
  {
    name: "remove_medications",
    description: "Remove medication names from the current chat session.",
    input_schema: {
      type: "object",
      properties: { names: { type: "array", items: { type: "string" } } },
      required: ["names"],
    },
  },
  {
    name: "clear_medications",
    description: "Clear the current medication list.",
    input_schema: { type: "object", properties: {} },
  },
  {
    name: "list_medications",
    description: "List current medications.",
    input_schema: { type: "object", properties: {} },
  },
  {
    name: "normalize_medications",
    description: "Normalize medication names through the local alias index.",
    input_schema: {
      type: "object",
      properties: { names: { type: "array", items: { type: "string" } } },
      required: ["names"],
    },
  },
  {
    name: "lookup_pair",
    description: "Look up a known local DDI reference pair.",
    input_schema: {
      type: "object",
      properties: {
        drug_a: { type: "string" },
        drug_b: { type: "string" },
        limit: { type: "integer" },
      },
      required: ["drug_a", "drug_b"],
    },
  },
  {
    name: "list_interactions_for_drug",
    description:
      "List known local DDI reference interactions involving one medication, ranked by severity and evidence count. Optional filters: min_severity (Major|Moderate|Minor), region (us|eu|india), risk_flag (free-text substring match against curated risk-flag notes).",
    input_schema: {
      type: "object",
      properties: {
        drug: { type: "string" },
        limit: { type: "integer" },
        min_severity: { type: "string" },
        region: { type: "string" },
        risk_flag: { type: "string" },
      },
      required: ["drug"],
    },
  },
  {
    name: "search_interactions_by_mechanism",
    description:
      "Source-text search over the curated mechanism_or_rationale and interaction_category fields on ddi_raw_signal. Use for questions like 'CYP3A4 inhibition interactions' or 'QT prolongation pairs'. Optional filters: drug (partner anchor), region, min_severity. Note: mechanism text varies in wording across source CSVs and may be inconsistent or missing — results are a hint, not a clean ontology.",
    input_schema: {
      type: "object",
      properties: {
        query: { type: "string" },
        drug: { type: "string" },
        region: { type: "string" },
        min_severity: { type: "string" },
        limit: { type: "integer" },
      },
      required: ["query"],
    },
  },
  {
    name: "search_interactions",
    description:
      "Search the local DDI reference DB across all pairs by any combination of filters: drug (partner filter), effect (adverse-effect substring), min_severity, region, risk_flag. Use this for global questions like 'what causes hyperkalemia?' or 'major India-flagged interactions'. Results are ranked by severity then evidence count.",
    input_schema: {
      type: "object",
      properties: {
        drug: { type: "string" },
        effect: { type: "string" },
        min_severity: { type: "string" },
        region: { type: "string" },
        risk_flag: { type: "string" },
        limit: { type: "integer" },
      },
    },
  },
  {
    name: "get_pair_effects",
    description: "Get adverse effects for a local DDI reference pair.",
    input_schema: {
      type: "object",
      properties: {
        drug_a: { type: "string" },
        drug_b: { type: "string" },
        limit: { type: "integer" },
      },
      required: ["drug_a", "drug_b"],
    },
  },
  {
    name: "get_raw_signals",
    description: "Get raw supporting DDI signal rows for a pair.",
    input_schema: {
      type: "object",
      properties: {
        drug_a: { type: "string" },
        drug_b: { type: "string" },
        limit: { type: "integer" },
      },
      required: ["drug_a", "drug_b"],
    },
  },
  {
    name: "bulk_check_pairs",
    description:
      "For each candidate medication, look up every known DDI pair against the comparison list. If `against` is omitted, the current session medications are used. Returns one row per candidate with normalized form, list of findings, highest severity found, and an unresolved flag. Useful for OCR confirmation flows and 'is X safe to add?' questions.",
    input_schema: {
      type: "object",
      properties: {
        candidates: { type: "array", items: { type: "string" } },
        against: { type: "array", items: { type: "string" } },
      },
      required: ["candidates"],
    },
  },
  {
    name: "build_structured_report",
    description: "Build a deterministic safety report for supplied or session medications.",
    input_schema: {
      type: "object",
      properties: { medication_names: { type: "array", items: { type: "string" } } },
    },
  },
  {
    name: "search_drug_aliases",
    description: "Search local medication aliases.",
    input_schema: {
      type: "object",
      properties: { query: { type: "string" }, limit: { type: "integer" } },
      required: ["query"],
    },
  },
  {
    name: "list_drugs_by_category",
    description:
      "Browse curated drug categories from the normalization catalog (e.g., 'cardiovascular', 'antibiotic', 'anticoagulant_antiplatelet'). Without a category, returns all categories with their drug counts. With a category (substring match, case-insensitive), returns canonical drugs in that category. Useful for catalog/dataset QA and OCR exploration.",
    input_schema: {
      type: "object",
      properties: { category: { type: "string" }, limit: { type: "integer" } },
    },
  },
  {
    name: "list_aliases_for_drug",
    description:
      "List every alias (canonical, brand, generic, regional) mapped to a single canonical drug. Use this for questions like 'what brands map to paracetamol?' Input is normalized through the alias index first, so brand or OCR strings work as input.",
    input_schema: {
      type: "object",
      properties: { drug: { type: "string" }, limit: { type: "integer" } },
      required: ["drug"],
    },
  },
  {
    name: "get_common_medicine_profile",
    description: "Look up India common-medicine metadata for a brand/generic/user-entered medicine name.",
    input_schema: {
      type: "object",
      properties: { name: { type: "string" }, limit: { type: "integer" } },
      required: ["name"],
    },
  },
  {
    name: "search_common_medicines",
    description:
      "Search India common-medicine metadata by name/brand/use plus optional structured filters: therapeutic_category (e.g., 'analgesic'), otc_or_rx ('OTC' or 'Rx'), nlem_or_jan_aushadhi (e.g., 'NLEM', 'Jan Aushadhi'), risk_flag (substring of patient_risk_flags_india such as 'pregnancy', 'renal', 'liver'). At least one of query or a filter must be provided.",
    input_schema: {
      type: "object",
      properties: {
        query: { type: "string" },
        therapeutic_category: { type: "string" },
        otc_or_rx: { type: "string" },
        nlem_or_jan_aushadhi: { type: "string" },
        risk_flag: { type: "string" },
        limit: { type: "integer" },
      },
    },
  },
  {
    name: "severity_consensus",
    description: "Return per-region severity and rolled-up severity for a pair.",
    input_schema: {
      type: "object",
      properties: { drug_a: { type: "string" }, drug_b: { type: "string" } },
      required: ["drug_a", "drug_b"],
    },
  },
  {
    name: "find_pairs_by_effect",
    description: "Find current-session pairs with effects matching a query.",
    input_schema: {
      type: "object",
      properties: { effect: { type: "string" }, limit: { type: "integer" } },
      required: ["effect"],
    },
  },
  {
    name: "get_full_raw_signals",
    description:
      "Get full raw supporting DDI signal rows for a pair, including source file, source row, mechanism, flags, and source URLs.",
    input_schema: {
      type: "object",
      properties: {
        drug_a: { type: "string" },
        drug_b: { type: "string" },
        limit: { type: "integer" },
      },
      required: ["drug_a", "drug_b"],
    },
  },
  {
    name: "list_evidence_sources",
    description: "List DDI source files loaded into the evidence SQLite artifact with import counts.",
    input_schema: { type: "object", properties: {} },
  },
  {
    name: "list_import_issues",
    description: "List unresolved DDI import rows for artifact/debug review.",
    input_schema: {
      type: "object",
      properties: {
        source_file: { type: "string" },
        query: { type: "string" },
        limit: { type: "integer" },
      },
    },
  },
  {
    name: "evidence_about",
    description: "Explain local evidence sources, severity scale, or limitations.",
    input_schema: {
      type: "object",
      properties: { topic: { type: "string" } },
      required: ["topic"],
    },
  },
  {
    name: "current_session_summary",
    description: "Return provider and session summary.",
    input_schema: { type: "object", properties: {} },
  },
];

// ---------- Provider transforms ----------

export function toAnthropicTools(): {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}[] {
  return TOOL_SCHEMAS.map((s) => ({
    name: s.name,
    description: s.description,
    input_schema: s.input_schema,
  }));
}

export function toGeminiTools(): { functionDeclarations: object[] }[] {
  const declarations = TOOL_SCHEMAS.map((s) => ({
    name: s.name,
    description: s.description,
    parameters: s.input_schema,
  }));
  return [{ functionDeclarations: declarations }];
}

// ---------- Dispatch ----------

export async function dispatch(
  name: string,
  args: Record<string, unknown> | undefined,
  ctx: { store: MedicationSafetyStore; session: ChatSession },
): Promise<Record<string, unknown>> {
  const a = { ...(args ?? {}) };
  const started = performance.now();
  const record: ToolCallRecord = { name, args: { ...a } };
  let result: Record<string, unknown>;
  try {
    result = await dispatchInner(name, a, ctx);
    record.result = result;
  } catch (err) {
    const e = err as Error;
    result = { error: e.message, code: e.name };
    record.result = result;
    record.error = e.message;
  } finally {
    record.duration_ms = Math.round(performance.now() - started);
    ctx.session.last_trace.push(record);
  }
  return result;
}

async function dispatchInner(
  name: string,
  args: Record<string, unknown>,
  { store, session }: { store: MedicationSafetyStore; session: ChatSession },
): Promise<Record<string, unknown>> {
  switch (name) {
    case "add_medications":
      return addMedications(stringList(args.names), { store, session });
    case "remove_medications":
      return removeMedications(stringList(args.names), { store, session });
    case "clear_medications":
      session.medications = [];
      session.last_report = null;
      return { cleared: true };
    case "list_medications":
      return { medications: session.medications.map(normalizedToDict) };
    case "normalize_medications":
      return {
        medications: store
          .normalizeMedicationNames(stringList(args.names))
          .map(normalizedToDict),
      };
    case "lookup_pair": {
      const interaction = await store.lookupKnownInteraction(
        String(args.drug_a),
        String(args.drug_b),
        limitOf(args, 8),
      );
      return interactionSummary(interaction);
    }
    case "list_interactions_for_drug": {
      const { normalized, interactions } = await store.listInteractionsForDrug(
        String(args.drug),
        {
          limit: limitOf(args, 20),
          effect_limit: 3,
          min_severity: optionalStr(args.min_severity),
          region: optionalStr(args.region),
          risk_flag: optionalStr(args.risk_flag),
        },
      );
      return {
        drug: normalizedToDict(normalized),
        count: interactions.length,
        interactions: interactions.map((i) =>
          drugInteractionSummary(normalized.canonical_name, i),
        ),
      };
    }
    case "search_interactions_by_mechanism":
      // Not yet implemented in the TS port — falls back to an explicit note.
      return {
        query: String(args.query),
        filters: {
          drug: optionalStr(args.drug),
          region: optionalStr(args.region),
          min_severity: optionalStr(args.min_severity),
        },
        count: 0,
        matches: [],
        note: "Mechanism search lands in a follow-up port slice; the SQL view is available, the TS wrapper is pending.",
      };
    case "search_interactions": {
      const result = await store.searchInteractions({
        drug: optionalStr(args.drug),
        effect: optionalStr(args.effect),
        min_severity: optionalStr(args.min_severity),
        region: optionalStr(args.region),
        risk_flag: optionalStr(args.risk_flag),
        limit: limitOf(args, 20),
        effect_limit: 3,
      });
      const anchor = result.drug_normalization?.canonical_name ?? null;
      return {
        filters: result.filters,
        drug_normalization: result.drug_normalization
          ? normalizedToDict(result.drug_normalization)
          : null,
        count: result.interactions.length,
        interactions: result.interactions.map((i) => drugInteractionSummary(anchor, i)),
      };
    }
    case "get_pair_effects": {
      const interaction = await store.lookupKnownInteraction(
        String(args.drug_a),
        String(args.drug_b),
        limitOf(args, 20),
      );
      return { effects: interaction.effects.map(effectToDict) };
    }
    case "get_raw_signals": {
      const interaction = await store.lookupKnownInteraction(
        String(args.drug_a),
        String(args.drug_b),
        3,
        limitOf(args, 20),
      );
      return { raw_signals: interaction.raw_signals.map(rawToDict) };
    }
    case "get_full_raw_signals": {
      const interaction = await store.lookupKnownInteraction(
        String(args.drug_a),
        String(args.drug_b),
        3,
        limitOf(args, 20),
      );
      return {
        found: interaction.found,
        drug_a: interaction.drug_a,
        drug_b: interaction.drug_b,
        raw_signals: interaction.raw_signals.map(rawFullToDict),
      };
    }
    case "bulk_check_pairs": {
      // Lightweight TS port — relies on lookupKnownInteraction per (candidate, partner).
      const candidates = stringList(args.candidates);
      const against =
        args.against !== undefined ? stringList(args.against) : session.medicationInputs();
      const candidateNormalized = store.normalizeMedicationNames(candidates);
      const againstNormalized = store.normalizeMedicationNames(against);
      const againstCanonicals: string[] = [];
      const seen = new Set<string>();
      for (const item of againstNormalized) {
        if (item.resolved && item.canonical_name && !seen.has(item.canonical_name)) {
          againstCanonicals.push(item.canonical_name);
          seen.add(item.canonical_name);
        }
      }
      let overallRank = 0;
      let overallSev = "None";
      const candidateResults: Record<string, unknown>[] = [];
      const unresolvedItems: NormalizedMedication[] = [];
      for (const candidate of candidateNormalized) {
        if (!candidate.resolved || !candidate.canonical_name) {
          unresolvedItems.push(candidate);
          candidateResults.push({
            candidate: candidate.input_name,
            normalized: normalizedToDict(candidate),
            findings: [],
            highest_severity: "None",
            interaction_count: 0,
            unresolved: true,
          });
          continue;
        }
        const findings: Record<string, unknown>[] = [];
        let bestRank = 0;
        let bestSev = "None";
        for (const partner of againstCanonicals) {
          if (partner === candidate.canonical_name) continue;
          const interaction = await store.lookupKnownInteraction(
            candidate.canonical_name,
            partner,
            3,
            0,
          );
          if (!interaction.found) continue;
          findings.push(knownInteractionToDict(interaction));
          const r = severityRankSimple(interaction.severity);
          if (r > bestRank) {
            bestRank = r;
            bestSev = interaction.severity ?? "None";
          }
        }
        if (bestRank > overallRank) {
          overallRank = bestRank;
          overallSev = bestSev;
        }
        candidateResults.push({
          candidate: candidate.input_name,
          normalized: normalizedToDict(candidate),
          findings,
          highest_severity: bestSev,
          interaction_count: findings.length,
          unresolved: false,
        });
      }
      return {
        against: againstNormalized.map(normalizedToDict),
        candidates: candidateResults,
        unresolved_candidates: unresolvedItems.map(normalizedToDict),
        overall_severity: overallSev,
      };
    }
    case "build_structured_report": {
      const names =
        args.medication_names !== undefined
          ? stringList(args.medication_names)
          : session.medicationInputs();
      const report = await store.buildStructuredReport(names, limitOf(args, 8));
      session.last_report = report;
      return report as unknown as Record<string, unknown>;
    }
    case "search_drug_aliases": {
      const query = String(args.query);
      return { query, matches: store.searchDrugAliases(query, limitOf(args, 10)) };
    }
    case "list_aliases_for_drug":
      // Not implemented in the TS port yet — return an empty stub.
      return {
        drug: normalizedToDict(store.normalizeMedicationNames([String(args.drug)])[0]),
        count: 0,
        aliases: [],
        by_type: {},
        note: "list_aliases_for_drug TS wrapper pending.",
      };
    case "list_drugs_by_category":
      return {
        category: optionalStr(args.category),
        categories: [],
        drugs: [],
        count: 0,
        note: "list_drugs_by_category TS wrapper pending.",
      };
    case "get_common_medicine_profile":
      return await store.getCommonMedicineProfile(String(args.name), limitOf(args, 10)) as unknown as Record<string, unknown>;
    case "search_common_medicines": {
      const query = optionalStr(args.query);
      const therapeutic_category =
        optionalStr(args.therapeutic_category) ?? optionalStr(args.category);
      const otc_or_rx = optionalStr(args.otc_or_rx);
      const nlem = optionalStr(args.nlem_or_jan_aushadhi);
      const risk_flag = optionalStr(args.risk_flag);
      const matches = await store.searchCommonMedicines({
        query,
        therapeutic_category,
        otc_or_rx,
        nlem_or_jan_aushadhi: nlem,
        risk_flag,
        limit: limitOf(args, 10),
      });
      return {
        query,
        filters: { therapeutic_category, otc_or_rx, nlem_or_jan_aushadhi: nlem, risk_flag },
        matches,
      };
    }
    case "severity_consensus": {
      const interaction = await store.lookupKnownInteraction(
        String(args.drug_a),
        String(args.drug_b),
        8,
        1000,
      );
      if (!interaction.found) {
        return { found: false, drug_a: interaction.drug_a, drug_b: interaction.drug_b };
      }
      const byRegionRank = new Map<string, { severity: string; rank: number }>();
      for (const raw of interaction.raw_signals) {
        const r = severityRankSimple(raw.severity);
        const cur = byRegionRank.get(raw.region);
        if (!cur || r > cur.rank) {
          byRegionRank.set(raw.region, { severity: raw.severity, rank: r });
        }
      }
      const byRegion: Record<string, string> = {};
      const keys = Array.from(byRegionRank.keys()).sort();
      for (const k of keys) byRegion[k] = byRegionRank.get(k)!.severity;
      const unique = new Set(Object.values(byRegion));
      return {
        found: true,
        drug_a: interaction.drug_a,
        drug_b: interaction.drug_b,
        single_region: keys.length <= 1,
        by_region: byRegion,
        rolled_up: interaction.severity,
        disagreement: unique.size > 1,
      };
    }
    case "find_pairs_by_effect": {
      const query = normalizeLookupText(String(args.effect));
      if (!query) return { matches: [] };
      const report = await store.buildStructuredReport(session.medicationInputs(), 100);
      const matches: Record<string, unknown>[] = [];
      const limit = limitOf(args, 10);
      for (const finding of report.findings) {
        const phrases: string[] = [];
        for (const eff of finding.effects) {
          const norm = normalizeLookupText(eff.adverse_effect);
          if (norm.includes(query) || query.includes(norm)) phrases.push(eff.adverse_effect);
        }
        if (phrases.length > 0) {
          matches.push({
            drug_a: finding.drug_a,
            drug_b: finding.drug_b,
            severity: finding.severity,
            regions: finding.source_regions,
            matched_phrases: phrases.slice(0, 5),
          });
        }
        if (matches.length >= limit) break;
      }
      return { matches };
    }
    case "list_evidence_sources":
      return { sources: await store.listEvidenceSources() };
    case "list_import_issues":
      return {
        issues: await store.listImportIssues({
          source_file: optionalStr(args.source_file),
          query: optionalStr(args.query),
          limit: limitOf(args, 20),
        }),
      };
    case "evidence_about":
      return evidenceAbout(String(args.topic));
    case "current_session_summary":
      return {
        provider: session.provider_name,
        model: session.provider_model,
        meds_count: session.medications.length,
        last_report_id: session.last_report ? "set" : null,
        privacy_note:
          session.privacy_mode === "cloud"
            ? `meds and questions leave device -> ${session.provider_name}`
            : "100% offline -> template",
      };
  }
  return { error: `Unknown tool: ${name}`, code: "unknown_tool" };
}

// ---------- helpers (verbatim from Python) ----------

function addMedications(
  names: string[],
  { store, session }: { store: MedicationSafetyStore; session: ChatSession },
): Record<string, unknown> {
  const normalized = store.normalizeMedicationNames(names);
  const existingInputs = new Set(session.medications.map((m) => m.input_name.toLowerCase()));
  const existingCanonicals = new Set(
    session.medications.map((m) => m.canonical_name).filter((v): v is string => !!v),
  );
  const added: Record<string, unknown>[] = [];
  const already_present: Record<string, unknown>[] = [];
  const unresolved: Record<string, unknown>[] = [];
  for (const item of normalized) {
    if (
      existingInputs.has(item.input_name.toLowerCase()) ||
      (item.canonical_name && existingCanonicals.has(item.canonical_name))
    ) {
      already_present.push(normalizedToDict(item));
      continue;
    }
    session.medications.push(item);
    existingInputs.add(item.input_name.toLowerCase());
    if (item.canonical_name) existingCanonicals.add(item.canonical_name);
    (item.resolved ? added : unresolved).push(normalizedToDict(item));
  }
  session.last_report = null;
  return { added, already_present, unresolved };
}

function removeMedications(
  names: string[],
  { store, session }: { store: MedicationSafetyStore; session: ChatSession },
): Record<string, unknown> {
  const normalized = store.normalizeMedicationNames(names);
  const removeInputs = new Set(normalized.map((m) => m.input_name.toLowerCase()));
  const removeCanonicals = new Set(
    normalized.map((m) => m.canonical_name).filter((v): v is string => !!v),
  );
  const kept: NormalizedMedication[] = [];
  const removed: Record<string, unknown>[] = [];
  const removedInputs = new Set<string>();
  const removedCanonicals = new Set<string>();
  for (const item of session.medications) {
    if (
      removeInputs.has(item.input_name.toLowerCase()) ||
      (item.canonical_name && removeCanonicals.has(item.canonical_name))
    ) {
      removed.push(normalizedToDict(item));
      removedInputs.add(item.input_name.toLowerCase());
      if (item.canonical_name) removedCanonicals.add(item.canonical_name);
    } else {
      kept.push(item);
    }
  }
  session.medications = kept;
  session.last_report = null;
  const not_found: string[] = [];
  for (const item of normalized) {
    const inMatch = removedInputs.has(item.input_name.toLowerCase());
    const canMatch = item.canonical_name ? removedCanonicals.has(item.canonical_name) : false;
    if (!inMatch && !canMatch) not_found.push(item.input_name);
  }
  return { removed, not_found };
}

function evidenceAbout(topic: string): Record<string, unknown> {
  const t = topic.toLowerCase().trim();
  const content: Record<string, string> = {
    sources:
      "MedLens uses local SQLite artifacts built from curated regional DDI-ADE CSV files for this MVP.",
    severity_scale:
      "Severity rolls up to the highest local signal: Major, Moderate, Minor, or None.",
    limitations:
      "This is screening/reference evidence only, not patient-specific medical advice or a diagnosis.",
  };
  return { topic: t, text: content[t] ?? content.limitations };
}

function interactionSummary(i: KnownInteraction): Record<string, unknown> {
  return {
    found: i.found,
    drug_a: i.drug_a,
    drug_b: i.drug_b,
    severity: i.severity,
    row_count: i.row_count,
    regions: i.source_regions,
    top_effects: i.effects.map(effectToDict),
  };
}

function drugInteractionSummary(
  canonical: string | null,
  i: KnownInteraction,
): Record<string, unknown> {
  const partner = i.drug_a === canonical ? i.drug_b : i.drug_a;
  return {
    drug: canonical,
    partner,
    drug_a: i.drug_a,
    drug_b: i.drug_b,
    severity: i.severity,
    row_count: i.row_count,
    regions: i.source_regions,
    top_effects: i.effects.map(effectToDict),
    source_urls: i.source_urls,
  };
}

function normalizedToDict(item: NormalizedMedication): Record<string, unknown> {
  return {
    input: item.input_name,
    input_name: item.input_name,
    normalized: item.canonical_name,
    canonical_name: item.canonical_name,
    status: item.resolved ? "resolved" : "unresolved",
    resolved: item.resolved,
    matched_alias: item.matched_alias,
  };
}

function effectToDict(e: InteractionEffect): Record<string, unknown> {
  return {
    adverse_effect: e.adverse_effect,
    severity: e.severity,
    count: e.row_count,
    row_count: e.row_count,
    regions: e.source_regions,
  };
}

function rawToDict(raw: RawDdiSignal): Record<string, unknown> {
  return {
    region: raw.region,
    severity: raw.severity,
    source_basis: raw.source_basis,
    source_url: raw.source_urls,
    mechanism: raw.mechanism_or_rationale,
    caveats: raw.use_case_note,
  };
}

function rawFullToDict(raw: RawDdiSignal): Record<string, unknown> {
  return { ...raw } as unknown as Record<string, unknown>;
}

function knownInteractionToDict(i: KnownInteraction): Record<string, unknown> {
  return {
    found: i.found,
    evidence_source: i.evidence_source,
    drug_a: i.drug_a,
    drug_b: i.drug_b,
    severity: i.severity,
    row_count: i.row_count,
    source_regions: i.source_regions,
    evidence_bases: i.evidence_bases,
    source_bases: i.source_bases,
    source_urls: i.source_urls,
    mechanisms: i.mechanisms,
    risk_flags: i.risk_flags,
    dataset_types: i.dataset_types,
    use_case_notes: i.use_case_notes,
    effects: i.effects.map(effectToDict),
    raw_signals: i.raw_signals.map(rawFullToDict),
  };
}

function optionalStr(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  const text = String(value).trim();
  return text || null;
}

function stringList(value: unknown): string[] {
  if (value === undefined || value === null) return [];
  if (typeof value === "string") {
    const t = value.trim();
    return t ? [t] : [];
  }
  if (Array.isArray(value)) {
    return value.map((v) => String(v).trim()).filter((v) => v.length > 0);
  }
  return [];
}

function limitOf(args: Record<string, unknown>, fallback: number): number {
  const raw = args.limit;
  const n = typeof raw === "number" ? raw : raw === undefined ? fallback : Number(raw);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(1, Math.min(1000, Math.floor(n)));
}

function severityRankSimple(severity: string | null): number {
  return (
    ({ Major: 3, Moderate: 2, Minor: 1, high: 3, medium: 2, moderate: 2, low: 1 } as Record<
      string,
      number
    >)[severity ?? ""] ?? 0
  );
}
