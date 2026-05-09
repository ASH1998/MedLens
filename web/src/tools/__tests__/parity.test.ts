// Parity test against the real local SQLite artifacts. Skips if either
// `data/artifacts/normalization.sqlite` or `data/artifacts/evidence.mobile.sqlite`
// is missing — CI builds without artifacts will not fail.

import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import initSqlJs from "sql.js";
import { MedicationSafetyStore } from "../safety-store";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..", "..");
const requireFn = createRequire(import.meta.url);
const SQL_WASM_PATH = requireFn.resolve("sql.js/dist/sql-wasm.wasm");
const NORMALIZATION = path.join(REPO_ROOT, "data", "artifacts", "normalization.sqlite");
const EVIDENCE = path.join(REPO_ROOT, "data", "artifacts", "evidence.mobile.sqlite");

const haveArtifacts = fs.existsSync(NORMALIZATION) && fs.existsSync(EVIDENCE);
const describeIf = haveArtifacts ? describe : describe.skip;

describeIf("MedicationSafetyStore against real artifacts", () => {
  it("normalizes Advil → ibuprofen and Warfarin → warfarin", async () => {
    const store = await openStore();
    const result = store.normalizeMedicationNames(["Advil", "Warfarin", "Mystery Pill"]);
    expect(result[0].canonical_name).toBe("ibuprofen");
    expect(result[0].resolved).toBe(true);
    expect(result[1].canonical_name).toBe("warfarin");
    expect(result[1].resolved).toBe(true);
    expect(result[2].resolved).toBe(false);
    expect(result[2].canonical_name).toBeNull();
  });

  it("looks up ibuprofen + warfarin as a Major finding", async () => {
    const store = await openStore();
    const interaction = await store.lookupKnownInteraction("Advil", "Warfarin");
    expect(interaction.found).toBe(true);
    expect(interaction.severity).toBe("Major");
    expect(interaction.row_count).toBeGreaterThan(0);
    expect(interaction.effects.length).toBeGreaterThan(0);
  });

  it("builds a structured report with the same shape as the Python CLI", async () => {
    const store = await openStore();
    const report = await store.buildStructuredReport([
      "Advil",
      "Warfarin",
      "Paracetamol",
      "Mystery Pill",
    ]);
    expect(report.checked_pair_count).toBe(3);
    expect(report.unresolved_medications.map((u) => u.input_name)).toEqual(["Mystery Pill"]);
    expect(report.overall_severity).toBe("Major");
    expect(report.evidence_status).toBe("verified_reference_findings_with_unresolved_inputs");
    // Findings must be ranked by severity then row count.
    expect(report.findings.length).toBeGreaterThan(0);
    expect(report.findings[0].severity).toBe("Major");
  });

  it("lists evidence sources from evidence.mobile.sqlite", async () => {
    const store = await openStore();
    const sources = await store.listEvidenceSources();
    expect(sources.length).toBeGreaterThan(0);
    expect(sources.some((s) => s.region.length > 0)).toBe(true);
  });
});

async function openStore(): Promise<MedicationSafetyStore> {
  const SQL = await initSqlJs({ locateFile: () => SQL_WASM_PATH });
  const normalization = new SQL.Database(new Uint8Array(fs.readFileSync(NORMALIZATION)));
  let evidence: InstanceType<typeof SQL.Database> | null = null;
  return new MedicationSafetyStore({
    normalization,
    async evidence() {
      if (!evidence) evidence = new SQL.Database(new Uint8Array(fs.readFileSync(EVIDENCE)));
      return evidence;
    },
  });
}
