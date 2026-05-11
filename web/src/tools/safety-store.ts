// Verbatim TS port of `medlens/tools/local_safety.py:MedicationSafetyStore`.
// SQL strings, severity ranking, region canonicalization, sort keys, evidence
// status, report limitations, and report assembly are translated literally so
// browser results match the Python CLI byte-for-byte for the same DBs.

import type { Database } from "sql.js";
import { normalizeLookupText } from "../db/normalize";
import {
  jsonTuple,
  relationExists,
  selectAll,
  selectOne,
} from "./sql";
import type {
  CommonMedicineRow,
  EvidenceImportFile,
  InteractionEffect,
  InteractionSearchResult,
  KnownInteraction,
  MedicationSafetyReport,
  NormalizedMedication,
  RawDdiSignal,
} from "./types";

export interface SafetyStoreDbs {
  normalization: Database;
  /** Lazy: opened on first interaction tool call. */
  evidence(): Promise<Database>;
}

const REGION_ALIASES: Record<string, readonly string[]> = {
  us: ["us"],
  usa: ["us"],
  "united states": ["us"],
  "united states of america": ["us"],
  eu: ["eu/eea"],
  eea: ["eu/eea"],
  europe: ["eu/eea"],
  "european union": ["eu/eea"],
  "eu/eea": ["eu/eea"],
  in: ["india", "india_expanded", "india_common_generic"],
  india: ["india", "india_expanded", "india_common_generic"],
  india_expanded: ["india_expanded"],
  india_common_generic: ["india_common_generic"],
};

export function canonicalizeRegion(region: string): string[] {
  const key = (region ?? "").toLowerCase().trim();
  if (!key) return [];
  return [...(REGION_ALIASES[key] ?? [key])];
}

export function inputSeverityRank(severity: string): number {
  const key = (severity ?? "").toLowerCase().trim();
  return (
    ({ major: 3, moderate: 2, minor: 1, high: 3, medium: 2, low: 1 } as Record<string, number>)[
      key
    ] ?? 0
  );
}

export function severityRank(severity: string | null | undefined): number {
  return ({ Major: 3, Moderate: 2, Minor: 1 } as Record<string, number>)[severity ?? ""] ?? 0;
}

function overallSeverity(findings: readonly KnownInteraction[]): string {
  if (findings.length === 0) return "None";
  let bestRank = 0;
  let best = "None";
  for (const f of findings) {
    const r = severityRank(f.severity);
    if (r > bestRank) {
      bestRank = r;
      best = f.severity ?? "None";
    }
  }
  return best;
}

function interactionSortKey(i: KnownInteraction): [number, number, string, string] {
  return [-severityRank(i.severity), -i.row_count, i.drug_a, i.drug_b];
}

function compareSortKey(
  a: [number, number, string, string],
  b: [number, number, string, string],
): number {
  for (let i = 0; i < a.length; i++) {
    if (a[i] < b[i]) return -1;
    if (a[i] > b[i]) return 1;
  }
  return 0;
}

function editDistance(a: string, b: string): number {
  if (a === b) return 0;
  if (!a) return b.length;
  if (!b) return a.length;
  const prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  const cur = new Array<number>(b.length + 1);
  for (let i = 1; i <= a.length; i++) {
    cur[0] = i;
    for (let j = 1; j <= b.length; j++) {
      cur[j] = Math.min(
        prev[j] + 1,
        cur[j - 1] + 1,
        prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1),
      );
    }
    for (let j = 0; j <= b.length; j++) prev[j] = cur[j];
  }
  return prev[b.length];
}

function dedupeResolvedMedications(
  items: readonly NormalizedMedication[],
): NormalizedMedication[] {
  const seen = new Set<string>();
  const out: NormalizedMedication[] = [];
  for (const item of items) {
    if (!item.resolved || !item.canonical_name) continue;
    if (seen.has(item.canonical_name)) continue;
    seen.add(item.canonical_name);
    out.push(item);
  }
  return out;
}

function evidenceStatus(
  findings: readonly KnownInteraction[],
  unresolved: readonly NormalizedMedication[],
  checkedPairCount: number,
): string {
  if (findings.length > 0 && unresolved.length === 0) return "verified_reference_findings";
  if (findings.length > 0 && unresolved.length > 0)
    return "verified_reference_findings_with_unresolved_inputs";
  if (checkedPairCount > 0 && unresolved.length > 0)
    return "no_reference_findings_with_unresolved_inputs";
  if (checkedPairCount > 0) return "no_reference_findings";
  return "insufficient_resolved_medications";
}

function reportLimitations(
  unresolved: readonly NormalizedMedication[],
  checkedPairCount: number,
  findings: readonly KnownInteraction[],
): string[] {
  const out: string[] = [
    "This report uses local DDI reference signals only; it is a screening output, not patient-specific medical advice.",
  ];
  if (unresolved.length > 0) {
    const names = unresolved.map((m) => m.input_name).join(", ");
    out.push(`Some medications could not be normalized and were not checked: ${names}.`);
  }
  if (checkedPairCount === 0) {
    out.push("Fewer than two medications were resolved, so no pairwise interaction check was possible.");
  }
  if (findings.length === 0 && checkedPairCount > 0) {
    out.push("No known/reference DDI signal was found locally for the resolved medication pairs.");
  }
  return out;
}

export interface InteractionFilterArgs {
  drug_canonical?: string | null;
  effect?: string | null;
  min_severity?: string | null;
  region?: string | null;
  risk_flag?: string | null;
}

function buildInteractionFilters(args: InteractionFilterArgs): {
  where: string;
  params: unknown[];
  needsEffectJoin: boolean;
} {
  const clauses: string[] = [];
  const params: unknown[] = [];
  let needsEffectJoin = false;

  if (args.drug_canonical) {
    clauses.push("(ki.drug_a = ? OR ki.drug_b = ?)");
    params.push(args.drug_canonical, args.drug_canonical);
  }
  if (args.min_severity) {
    const rank = inputSeverityRank(args.min_severity);
    if (rank > 0) {
      clauses.push("ki.severity_rank >= ?");
      params.push(rank);
    }
  }
  if (args.region) {
    const canonical = canonicalizeRegion(args.region);
    if (canonical.length > 0) {
      const sub = canonical.map(() => "ki.source_regions_json LIKE ?").join(" OR ");
      clauses.push(`(${sub})`);
      for (const value of canonical) params.push(`%"${value}"%`);
    }
  }
  if (args.risk_flag && args.risk_flag.trim()) {
    clauses.push("lower(ki.risk_flags_json) LIKE ?");
    params.push(`%${args.risk_flag.toLowerCase().trim()}%`);
  }
  if (args.effect && args.effect.trim()) {
    needsEffectJoin = true;
    clauses.push("lower(kie.adverse_effect) LIKE ?");
    params.push(`%${args.effect.toLowerCase().trim()}%`);
  }
  const where = clauses.length > 0 ? clauses.join(" AND ") : "1=1";
  return { where, params, needsEffectJoin };
}

interface RawDrugRow {
  id: number;
  canonical_name: string;
  alias: string;
}

export class MedicationSafetyStore {
  constructor(private dbs: SafetyStoreDbs) {}

  normalizeMedicationNames(names: readonly string[]): NormalizedMedication[] {
    const db = this.dbs.normalization;
    const out: NormalizedMedication[] = [];
    for (const name of names) {
      const normalized_input = normalizeLookupText(name);
      const row = selectOne<RawDrugRow>(
        db,
        `SELECT d.id AS id, d.canonical_name AS canonical_name, a.alias AS alias
         FROM drug_alias a JOIN drug d ON d.id = a.drug_id
         WHERE a.normalized_alias = ?`,
        [normalized_input],
      );
      if (row) {
        out.push({
          input_name: name,
          normalized_input,
          canonical_name: String(row.canonical_name),
          drug_id: Number(row.id),
          matched_alias: String(row.alias),
          resolved: true,
        });
      } else {
        out.push({
          input_name: name,
          normalized_input,
          canonical_name: null,
          drug_id: null,
          matched_alias: null,
          resolved: false,
        });
      }
    }
    return out;
  }

  searchDrugAliases(query: string, limit = 10): { canonical: string; aliases: string[] }[] {
    const normalized_query = normalizeLookupText(query);
    if (!normalized_query) return [];
    const pattern = `%${normalized_query}%`;
    const rows = selectAll<{ canonical_name: string; alias: string }>(
      this.dbs.normalization,
      `SELECT d.canonical_name AS canonical_name, a.alias AS alias
       FROM drug_alias a JOIN drug d ON d.id = a.drug_id
       WHERE a.normalized_alias LIKE ? OR d.canonical_name LIKE ?
       ORDER BY
         CASE WHEN a.normalized_alias = ? THEN 0 ELSE 1 END,
         LENGTH(a.alias),
         d.canonical_name,
         a.alias
       LIMIT ?`,
      [pattern, pattern, normalized_query, Math.max(1, limit * 4)],
    );
    const grouped = new Map<string, string[]>();
    for (const row of rows) {
      const can = String(row.canonical_name);
      const al = String(row.alias);
      const list = grouped.get(can) ?? [];
      if (!list.includes(al)) list.push(al);
      grouped.set(can, list);
      if (
        grouped.size >= limit &&
        Array.from(grouped.values()).every((items) => items.length >= 3)
      ) {
        break;
      }
    }
    return Array.from(grouped.entries())
      .slice(0, limit)
      .map(([canonical, aliases]) => ({ canonical, aliases: aliases.slice(0, 5) }))
      .concat(grouped.size === 0 ? this.strengthTrimmedDrugAliases(normalized_query, limit) : [])
      .concat(grouped.size === 0 ? this.fuzzyDrugAliases(normalized_query, limit) : []);
  }

  private strengthTrimmedDrugAliases(normalizedQuery: string, limit: number): { canonical: string; aliases: string[] }[] {
    const trimmed = normalizedQuery.replace(/\s*\d+(?:\s*(?:mg|mcg|g|ml|iu))?$/i, "").trim();
    if (!trimmed || trimmed === normalizedQuery || trimmed.length < 3) return [];
    const rows = selectAll<{ canonical_name: string; alias: string }>(
      this.dbs.normalization,
      `SELECT d.canonical_name AS canonical_name, a.alias AS alias
       FROM drug_alias a JOIN drug d ON d.id = a.drug_id
       WHERE a.normalized_alias = ? OR a.normalized_alias LIKE ?
       ORDER BY
         CASE WHEN a.normalized_alias = ? THEN 0 ELSE 1 END,
         LENGTH(a.alias),
         d.canonical_name,
         a.alias
       LIMIT ?`,
      [trimmed, `${trimmed} %`, trimmed, Math.max(1, limit * 4)],
    );
    const grouped = new Map<string, string[]>();
    for (const row of rows) {
      const canonical = String(row.canonical_name);
      const alias = String(row.alias);
      const aliases = grouped.get(canonical) ?? [];
      if (!aliases.includes(alias)) aliases.push(alias);
      grouped.set(canonical, aliases);
      if (grouped.size >= limit) break;
    }
    return Array.from(grouped.entries()).map(([canonical, aliases]) => ({
      canonical,
      aliases: aliases.slice(0, 5),
    }));
  }

  private fuzzyDrugAliases(normalizedQuery: string, limit: number): { canonical: string; aliases: string[] }[] {
    const queryLength = normalizedQuery.length;
    if (queryLength < 4 || queryLength > 40) return [];
    const rows = selectAll<{ canonical_name: string; alias: string; normalized_alias: string }>(
      this.dbs.normalization,
      `SELECT d.canonical_name AS canonical_name, a.alias AS alias, a.normalized_alias AS normalized_alias
       FROM drug_alias a JOIN drug d ON d.id = a.drug_id
       WHERE LENGTH(a.normalized_alias) BETWEEN ? AND ?`,
      [Math.max(1, queryLength - 2), queryLength + 2],
    );
    const ranked = rows
      .map((row) => {
        const alias = String(row.normalized_alias);
        const canonical = normalizeLookupText(String(row.canonical_name));
        const score = Math.min(editDistance(normalizedQuery, alias), editDistance(normalizedQuery, canonical));
        return { row, score };
      })
      .filter(({ score }) => score <= Math.max(1, Math.floor(queryLength / 4)))
      .sort((a, b) => a.score - b.score || String(a.row.alias).length - String(b.row.alias).length);

    const grouped = new Map<string, string[]>();
    for (const { row } of ranked) {
      const canonical = String(row.canonical_name);
      const alias = String(row.alias);
      const aliases = grouped.get(canonical) ?? [];
      if (!aliases.includes(alias)) aliases.push(alias);
      grouped.set(canonical, aliases);
      if (grouped.size >= limit) break;
    }
    return Array.from(grouped.entries()).map(([canonical, aliases]) => ({
      canonical,
      aliases: aliases.slice(0, 5),
    }));
  }

  async lookupKnownInteraction(
    drugA: string,
    drugB: string,
    effectLimit = 8,
    rawSignalLimit = 20,
  ): Promise<KnownInteraction> {
    const normalized = this.normalizeMedicationNames([drugA, drugB]);
    const [left, right] = normalized;
    const pair_a = left.canonical_name ?? left.normalized_input;
    const pair_b = right.canonical_name ?? right.normalized_input;
    const [drug_key_a, drug_key_b] = [pair_a, pair_b].slice().sort();

    if (!left.resolved || !right.resolved) {
      return notFound(drug_key_a, drug_key_b);
    }

    const evidence = await this.dbs.evidence();
    const row = selectOne<RawKnownInteractionRow>(
      evidence,
      `SELECT * FROM known_interaction WHERE drug_a = ? AND drug_b = ?`,
      [drug_key_a, drug_key_b],
    );
    if (!row) return notFound(drug_key_a, drug_key_b);

    const interaction_id = Number(row.id);
    const effectRows = selectAll<RawEffectRow>(
      evidence,
      `SELECT adverse_effect, severity, row_count, source_regions_json
       FROM known_interaction_effect
       WHERE known_interaction_id = ?
       ORDER BY severity_rank DESC, row_count DESC, adverse_effect
       LIMIT ?`,
      [interaction_id, effectLimit],
    );
    const effects: InteractionEffect[] = effectRows.map((e) => ({
      adverse_effect: String(e.adverse_effect),
      severity: String(e.severity),
      row_count: Number(e.row_count),
      source_regions: jsonTuple(e.source_regions_json),
    }));

    let raw_signals: RawDdiSignal[] = [];
    if (rawSignalLimit > 0 && relationExists(evidence, "ddi_raw_signal")) {
      const rawRows = selectAll<RawSignalRow>(
        evidence,
        `SELECT * FROM ddi_raw_signal
         WHERE known_interaction_id = ?
         ORDER BY severity_rank DESC, source_file, source_row_number
         LIMIT ?`,
        [interaction_id, rawSignalLimit],
      );
      raw_signals = rawRows.map(rawSignalToObj);
    }

    return {
      found: true,
      drug_a: String(row.drug_a),
      drug_b: String(row.drug_b),
      severity: String(row.severity),
      row_count: Number(row.row_count),
      source_regions: jsonTuple(row.source_regions_json),
      evidence_bases: jsonTuple(row.evidence_bases_json),
      source_bases: jsonTuple(row.source_bases_json),
      source_urls: jsonTuple(row.source_urls_json),
      mechanisms: jsonTuple(row.mechanisms_json),
      risk_flags: jsonTuple(row.risk_flags_json),
      dataset_types: jsonTuple(row.dataset_types_json),
      use_case_notes: jsonTuple(row.use_case_notes_json),
      effects,
      raw_signals,
      evidence_source: "ddi_reference",
    };
  }

  async listInteractionsForDrug(
    drug: string,
    options: {
      limit?: number;
      effect_limit?: number;
      min_severity?: string | null;
      region?: string | null;
      risk_flag?: string | null;
    } = {},
  ): Promise<{ normalized: NormalizedMedication; interactions: KnownInteraction[] }> {
    const limit = options.limit ?? 20;
    const effect_limit = options.effect_limit ?? 3;
    const [normalized] = this.normalizeMedicationNames([drug]);
    if (!normalized.resolved || !normalized.canonical_name) {
      return { normalized, interactions: [] };
    }
    const evidence = await this.dbs.evidence();
    const { where, params } = buildInteractionFilters({
      drug_canonical: normalized.canonical_name,
      effect: null,
      min_severity: options.min_severity ?? null,
      region: options.region ?? null,
      risk_flag: options.risk_flag ?? null,
    });
    const rows = selectAll<{ drug_a: string; drug_b: string }>(
      evidence,
      `SELECT ki.drug_a, ki.drug_b
       FROM known_interaction ki
       WHERE ${where}
       ORDER BY ki.severity_rank DESC, ki.row_count DESC, ki.drug_a, ki.drug_b
       LIMIT ?`,
      [...params, Math.max(1, limit)],
    );
    const interactions: KnownInteraction[] = [];
    for (const row of rows) {
      interactions.push(
        await this.lookupKnownInteraction(
          String(row.drug_a),
          String(row.drug_b),
          effect_limit,
          0,
        ),
      );
    }
    return { normalized, interactions };
  }

  async searchInteractions(args: {
    drug?: string | null;
    effect?: string | null;
    min_severity?: string | null;
    region?: string | null;
    risk_flag?: string | null;
    limit?: number;
    effect_limit?: number;
  }): Promise<InteractionSearchResult> {
    const limit = Math.max(1, Math.floor(args.limit ?? 20));
    const effect_limit = args.effect_limit ?? 3;
    const filters: Record<string, unknown> = {
      drug: args.drug ?? null,
      effect: args.effect ?? null,
      min_severity: args.min_severity ?? null,
      region: args.region ?? null,
      risk_flag: args.risk_flag ?? null,
      limit,
    };
    let drug_normalization: NormalizedMedication | null = null;
    let drug_canonical: string | null = null;
    if (args.drug && args.drug.trim()) {
      const [n] = this.normalizeMedicationNames([args.drug]);
      drug_normalization = n;
      if (!n.resolved || !n.canonical_name) {
        return { filters, drug_normalization, interactions: [] };
      }
      drug_canonical = n.canonical_name;
    }
    const evidence = await this.dbs.evidence();
    const { where, params, needsEffectJoin } = buildInteractionFilters({
      drug_canonical,
      effect: args.effect,
      min_severity: args.min_severity,
      region: args.region,
      risk_flag: args.risk_flag,
    });
    const join_sql = needsEffectJoin
      ? "JOIN known_interaction_effect kie ON kie.known_interaction_id = ki.id"
      : "";
    const distinct_sql = needsEffectJoin ? "DISTINCT" : "";
    const rows = selectAll<{
      drug_a: string;
      drug_b: string;
      severity_rank: number;
      row_count: number;
    }>(
      evidence,
      `SELECT ${distinct_sql} ki.drug_a, ki.drug_b, ki.severity_rank, ki.row_count
       FROM known_interaction ki
       ${join_sql}
       WHERE ${where}
       ORDER BY ki.severity_rank DESC, ki.row_count DESC, ki.drug_a, ki.drug_b
       LIMIT ?`,
      [...params, limit],
    );
    const interactions: KnownInteraction[] = [];
    for (const row of rows) {
      interactions.push(
        await this.lookupKnownInteraction(
          String(row.drug_a),
          String(row.drug_b),
          effect_limit,
          0,
        ),
      );
    }
    return { filters, drug_normalization, interactions };
  }

  async buildStructuredReport(
    medicationNames: readonly string[],
    effectLimit = 8,
  ): Promise<MedicationSafetyReport> {
    const normalized = this.normalizeMedicationNames(medicationNames);
    const unresolved = normalized.filter((m) => !m.resolved);
    const resolved_unique = dedupeResolvedMedications(normalized);
    let checked_pair_count = 0;
    const findings: KnownInteraction[] = [];

    for (let i = 0; i < resolved_unique.length; i++) {
      for (let j = i + 1; j < resolved_unique.length; j++) {
        const left = resolved_unique[i];
        const right = resolved_unique[j];
        if (!left.canonical_name || !right.canonical_name) continue;
        checked_pair_count += 1;
        const interaction = await this.lookupKnownInteraction(
          left.canonical_name,
          right.canonical_name,
          effectLimit,
        );
        if (interaction.found) findings.push(interaction);
      }
    }

    const ranked = findings
      .slice()
      .sort((a, b) => compareSortKey(interactionSortKey(a), interactionSortKey(b)));
    const overall_severity = overallSeverity(ranked);
    const limitations = reportLimitations(unresolved, checked_pair_count, ranked);

    return {
      input_medications: [...medicationNames],
      normalized_medications: normalized,
      unresolved_medications: unresolved,
      checked_pair_count,
      findings: ranked,
      overall_severity,
      evidence_status: evidenceStatus(ranked, unresolved, checked_pair_count),
      limitations,
    };
  }

  async listEvidenceSources(): Promise<EvidenceImportFile[]> {
    const evidence = await this.dbs.evidence();
    return selectAll<EvidenceImportFile>(
      evidence,
      `SELECT source_file, region, rows_seen, rows_imported, rows_unresolved, unique_pairs_imported
       FROM evidence_import_file
       ORDER BY source_file`,
    ).map((r) => ({
      source_file: String(r.source_file),
      region: String(r.region),
      rows_seen: Number(r.rows_seen),
      rows_imported: Number(r.rows_imported),
      rows_unresolved: Number(r.rows_unresolved),
      unique_pairs_imported: Number(r.unique_pairs_imported),
    }));
  }

  async listImportIssues(args: {
    source_file?: string | null;
    query?: string | null;
    limit?: number;
  }): Promise<
    {
      source_file: string;
      row_number: number;
      drug1: string;
      drug2: string;
      normalized_drug1: string;
      normalized_drug2: string;
      reason: string;
    }[]
  > {
    const evidence = await this.dbs.evidence();
    if (!relationExists(evidence, "ddi_import_issue")) return [];
    const clauses: string[] = [];
    const params: unknown[] = [];
    if (args.source_file) {
      clauses.push("source_file = ?");
      params.push(args.source_file);
    }
    if (args.query) {
      const normalized_query = normalizeLookupText(args.query);
      clauses.push("(normalized_drug1 LIKE ? OR normalized_drug2 LIKE ? OR drug1 LIKE ? OR drug2 LIKE ?)");
      const pattern = `%${normalized_query}%`;
      const text_pattern = `%${args.query.trim()}%`;
      params.push(pattern, pattern, text_pattern, text_pattern);
    }
    const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";
    const limit = Math.max(1, args.limit ?? 20);
    const rows = selectAll<{
      source_file: string;
      row_number: number;
      drug1: string;
      drug2: string;
      normalized_drug1: string;
      normalized_drug2: string;
      reason: string;
    }>(
      evidence,
      `SELECT source_file, row_number, drug1, drug2, normalized_drug1, normalized_drug2, reason
       FROM ddi_import_issue
       ${where}
       ORDER BY source_file, row_number
       LIMIT ?`,
      [...params, limit],
    );
    return rows.map((row) => ({
      source_file: String(row.source_file),
      row_number: Number(row.row_number),
      drug1: String(row.drug1),
      drug2: String(row.drug2),
      normalized_drug1: String(row.normalized_drug1),
      normalized_drug2: String(row.normalized_drug2),
      reason: String(row.reason),
    }));
  }

  /** India common-medicine profile by user/brand/generic name. */
  async getCommonMedicineProfile(name: string, limit = 10): Promise<{
    query: string;
    normalized: NormalizedMedication;
    aliases: string[];
    matches: CommonMedicineRow[];
  }> {
    let [normalized] = this.normalizeMedicationNames([name]);
    const aliases: string[] = [];
    const matches: CommonMedicineRow[] = [];
    if (normalized.resolved && normalized.drug_id !== null) {
      const rows = selectAll<RawCommonRow>(
        this.dbs.normalization,
        `SELECT m.*, d.canonical_name
         FROM india_common_medicine m JOIN drug d ON d.id = m.drug_id
         WHERE m.drug_id = ?
         ORDER BY m.source_row_number
         LIMIT ?`,
        [normalized.drug_id, Math.max(1, limit)],
      );
      for (const r of rows) matches.push(commonRowToObj(r));
      const aliasRows = selectAll<{ alias: string }>(
        this.dbs.normalization,
        `SELECT alias FROM drug_alias
         WHERE drug_id = ?
         ORDER BY
           CASE alias_type WHEN 'canonical' THEN 0 WHEN 'brand' THEN 1 ELSE 2 END,
           LENGTH(alias),
           alias
         LIMIT 20`,
        [normalized.drug_id],
      );
      for (const a of aliasRows) aliases.push(String(a.alias));
    }
    if (matches.length === 0 && !normalized.resolved) {
      const aliasMatch = this.searchDrugAliases(name, 1)[0];
      if (aliasMatch?.canonical) {
        [normalized] = this.normalizeMedicationNames([aliasMatch.canonical]);
        if (normalized.resolved && normalized.drug_id !== null) {
          const rows = selectAll<RawCommonRow>(
            this.dbs.normalization,
            `SELECT m.*, d.canonical_name
             FROM india_common_medicine m JOIN drug d ON d.id = m.drug_id
             WHERE m.drug_id = ?
             ORDER BY m.source_row_number
             LIMIT ?`,
            [normalized.drug_id, Math.max(1, limit)],
          );
          for (const r of rows) matches.push(commonRowToObj(r));
          const aliasRows = selectAll<{ alias: string }>(
            this.dbs.normalization,
            `SELECT alias FROM drug_alias
             WHERE drug_id = ?
             ORDER BY
               CASE alias_type WHEN 'canonical' THEN 0 WHEN 'brand' THEN 1 ELSE 2 END,
               LENGTH(alias),
               alias
             LIMIT 20`,
            [normalized.drug_id],
          );
          for (const a of aliasRows) aliases.push(String(a.alias));
        }
      }
    }
    if (matches.length === 0) {
      const fallback = await this.searchCommonMedicines({ query: name, limit });
      return { query: name, normalized, aliases, matches: fallback };
    }
    return { query: name, normalized, aliases, matches };
  }

  async searchCommonMedicines(args: {
    query?: string | null;
    therapeutic_category?: string | null;
    otc_or_rx?: string | null;
    nlem_or_jan_aushadhi?: string | null;
    risk_flag?: string | null;
    limit?: number;
  }): Promise<CommonMedicineRow[]> {
    const limit = Math.max(1, args.limit ?? 10);
    const clauses: string[] = [];
    const params: unknown[] = [];
    let normalized_query: string | null = null;
    if (args.query && args.query.trim()) {
      normalized_query = normalizeLookupText(args.query);
      if (normalized_query) {
        const pattern = `%${normalized_query}%`;
        const text_pattern = `%${args.query.toLowerCase().trim()}%`;
        clauses.push(
          "(m.normalized_generic_name LIKE ? OR lower(m.common_brand_examples_india) LIKE ? OR lower(m.common_daily_life_use_india) LIKE ? OR lower(m.therapeutic_category) LIKE ?)",
        );
        params.push(pattern, text_pattern, text_pattern, text_pattern);
      }
    }
    if (args.therapeutic_category && args.therapeutic_category.trim()) {
      clauses.push("lower(m.therapeutic_category) LIKE ?");
      params.push(`%${args.therapeutic_category.toLowerCase().trim()}%`);
    }
    if (args.otc_or_rx && args.otc_or_rx.trim()) {
      clauses.push("lower(m.otc_or_rx) LIKE ?");
      params.push(`%${args.otc_or_rx.toLowerCase().trim()}%`);
    }
    if (args.nlem_or_jan_aushadhi && args.nlem_or_jan_aushadhi.trim()) {
      clauses.push("lower(m.nlem_or_jan_aushadhi_presence) LIKE ?");
      params.push(`%${args.nlem_or_jan_aushadhi.toLowerCase().trim()}%`);
    }
    if (args.risk_flag && args.risk_flag.trim()) {
      clauses.push("lower(m.patient_risk_flags_india) LIKE ?");
      params.push(`%${args.risk_flag.toLowerCase().trim()}%`);
    }
    if (clauses.length === 0) return [];
    const where = clauses.join(" AND ");
    const order_exact = normalized_query ?? "";
    const rows = selectAll<RawCommonRow>(
      this.dbs.normalization,
      `SELECT m.*, d.canonical_name
       FROM india_common_medicine m JOIN drug d ON d.id = m.drug_id
       WHERE ${where}
       ORDER BY
         CASE WHEN m.normalized_generic_name = ? THEN 0 ELSE 1 END,
         d.canonical_name,
         m.source_row_number
       LIMIT ?`,
      [...params, order_exact, limit],
    );
    return rows.map(commonRowToObj);
  }
}

interface RawKnownInteractionRow {
  id: number;
  drug_a: string;
  drug_b: string;
  severity: string;
  severity_rank: number;
  row_count: number;
  source_regions_json: string;
  evidence_bases_json: string;
  source_bases_json: string;
  source_urls_json: string;
  mechanisms_json: string;
  risk_flags_json: string;
  dataset_types_json: string;
  use_case_notes_json: string;
}

interface RawEffectRow {
  adverse_effect: string;
  severity: string;
  row_count: number;
  source_regions_json: string;
}

interface RawSignalRow {
  source_file: string;
  source_row_number: number;
  source_signal_id: string | null;
  region: string;
  drug1_raw: string;
  drug2_raw: string;
  adverse_effect: string | null;
  severity: string;
  mechanism_or_rationale: string | null;
  interaction_category: string | null;
  interaction_direction: string | null;
  evidence_basis: string | null;
  source_basis: string | null;
  source_urls: string | null;
  population_relevance: string | null;
  patient_risk_flags: string | null;
  dataset_type: string | null;
  use_case_note: string | null;
}

function rawSignalToObj(raw: RawSignalRow): RawDdiSignal {
  return {
    source_file: String(raw.source_file),
    source_row_number: Number(raw.source_row_number),
    source_signal_id: String(raw.source_signal_id ?? ""),
    region: String(raw.region),
    drug1_raw: String(raw.drug1_raw),
    drug2_raw: String(raw.drug2_raw),
    adverse_effect: String(raw.adverse_effect ?? ""),
    severity: String(raw.severity),
    mechanism_or_rationale: String(raw.mechanism_or_rationale ?? ""),
    interaction_category: String(raw.interaction_category ?? ""),
    interaction_direction: String(raw.interaction_direction ?? ""),
    evidence_basis: String(raw.evidence_basis ?? ""),
    source_basis: String(raw.source_basis ?? ""),
    source_urls: String(raw.source_urls ?? ""),
    population_relevance: String(raw.population_relevance ?? ""),
    patient_risk_flags: String(raw.patient_risk_flags ?? ""),
    dataset_type: String(raw.dataset_type ?? ""),
    use_case_note: String(raw.use_case_note ?? ""),
  };
}

interface RawCommonRow {
  medicine_id: string;
  canonical_name: string;
  generic_or_common_name: string;
  composition_or_strength_pattern: string;
  dosage_form: string;
  therapeutic_category: string;
  common_daily_life_use_india: string;
  common_brand_examples_india: string;
  availability_context_india: string;
  otc_or_rx: string;
  nlem_or_jan_aushadhi_presence: string;
  india_relevance: string;
  patient_risk_flags_india: string;
  source_basis: string;
  source_urls: string;
  dataset_note: string;
}

function commonRowToObj(row: RawCommonRow): CommonMedicineRow {
  return {
    medicine_id: String(row.medicine_id),
    canonical_name: String(row.canonical_name),
    generic_or_common_name: String(row.generic_or_common_name),
    composition_or_strength_pattern: String(row.composition_or_strength_pattern),
    dosage_form: String(row.dosage_form),
    therapeutic_category: String(row.therapeutic_category),
    common_daily_life_use_india: String(row.common_daily_life_use_india),
    common_brand_examples_india: String(row.common_brand_examples_india),
    availability_context_india: String(row.availability_context_india),
    otc_or_rx: String(row.otc_or_rx),
    nlem_or_jan_aushadhi_presence: String(row.nlem_or_jan_aushadhi_presence),
    india_relevance: String(row.india_relevance),
    patient_risk_flags_india: String(row.patient_risk_flags_india),
    source_basis: String(row.source_basis),
    source_urls: String(row.source_urls),
    dataset_note: String(row.dataset_note),
  };
}

function notFound(drugA: string, drugB: string): KnownInteraction {
  return {
    found: false,
    drug_a: drugA,
    drug_b: drugB,
    severity: null,
    row_count: 0,
    source_regions: [],
    evidence_bases: [],
    source_bases: [],
    source_urls: [],
    mechanisms: [],
    risk_flags: [],
    dataset_types: [],
    use_case_notes: [],
    effects: [],
    raw_signals: [],
    evidence_source: "ddi_reference",
  };
}
