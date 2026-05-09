import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import initSqlJs from "sql.js";
import { runAgentTurn } from "../loop";
import { ChatSession } from "../../chat/session";
import { TemplateProvider } from "../../providers/template";
import { MedicationSafetyStore } from "../../tools/safety-store";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..", "..");
const requireFn = createRequire(import.meta.url);
const SQL_WASM_PATH = requireFn.resolve("sql.js/dist/sql-wasm.wasm");
const NORMALIZATION = path.join(REPO_ROOT, "data", "artifacts", "normalization.sqlite");
const EVIDENCE = path.join(REPO_ROOT, "data", "artifacts", "evidence.mobile.sqlite");

const haveArtifacts = fs.existsSync(NORMALIZATION) && fs.existsSync(EVIDENCE);
const describeIf = haveArtifacts ? describe : describe.skip;

describeIf("runAgentTurn with TemplateProvider", () => {
  it("round-trips a fixed medicine list through tools and returns a deterministic report", async () => {
    const store = await openStore();
    const session = new ChatSession();
    const result = await runAgentTurn({
      provider: new TemplateProvider(),
      session,
      store,
      user_message: "Advil and Warfarin",
    });

    const compact = result.final_text.replace(/\s+/g, " ");
    expect(compact).toContain("Overall local evidence severity: Major.");
    expect(compact).toContain("ibuprofen + warfarin is a Major finding.");
    expect(result.used_tools).toEqual([
      "normalize_medications",
      "add_medications",
      "build_structured_report",
    ]);
    expect(result.report?.overall_severity).toBe("Major");
  });

  it("handles what-meds-should-not-be-taken phrasing as a single-drug interaction lookup", async () => {
    const store = await openStore();
    const session = new ChatSession();
    const result = await runAgentTurn({
      provider: new TemplateProvider(),
      session,
      store,
      user_message: "what meds should not be taken with dolo ?",
    });

    expect(result.used_tools).toEqual(["list_interactions_for_drug"]);
    expect(result.trace[0]?.args).toEqual({ drug: "dolo", limit: 12 });
    expect(result.final_text.toLowerCase()).not.toContain("couldn't match this locally: not");
  });

  it("does not treat conversational filler as medication names", async () => {
    const store = await openStore();
    const session = new ChatSession();
    const result = await runAgentTurn({
      provider: new TemplateProvider(),
      session,
      store,
      user_message: "Hey, is there something wrong with dolo 6 and paracetamol ?",
    });

    expect(result.used_tools).toEqual([
      "normalize_medications",
      "search_drug_aliases",
      "add_medications",
      "build_structured_report",
    ]);
    expect(result.trace[0]?.args).toEqual({ names: ["dolo 6", "paracetamol"] });
    expect(result.trace[1]?.args).toEqual({ query: "dolo 6", limit: 5 });
    expect(result.final_text.toLowerCase()).not.toContain("hey");
    expect(result.final_text.toLowerCase()).not.toContain("something wrong");
    expect(result.final_text.toLowerCase()).not.toContain("couldn't match");
  });

  it("recovers a typo through local alias search before checking the pair", async () => {
    const store = await openStore();
    const session = new ChatSession();
    const result = await runAgentTurn({
      provider: new TemplateProvider(),
      session,
      store,
      user_message: "what about apirin and acenocoumarol",
    });

    expect(result.used_tools).toEqual([
      "normalize_medications",
      "search_drug_aliases",
      "add_medications",
      "build_structured_report",
    ]);
    expect(result.trace[0]?.args).toEqual({ names: ["apirin", "acenocoumarol"] });
    expect(result.trace[1]?.args).toEqual({ query: "apirin", limit: 5 });
    expect(result.final_text.toLowerCase()).toContain("aspirin");
    expect(result.final_text.toLowerCase()).toContain("acenocoumarol");
    expect(result.final_text.toLowerCase()).not.toContain("couldn't match");
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
