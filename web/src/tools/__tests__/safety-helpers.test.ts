import { describe, expect, it } from "vitest";
import {
  canonicalizeRegion,
  inputSeverityRank,
  severityRank,
} from "../safety-store";

describe("severity helpers", () => {
  it("severityRank matches Python _severity_rank", () => {
    expect(severityRank("Major")).toBe(3);
    expect(severityRank("Moderate")).toBe(2);
    expect(severityRank("Minor")).toBe(1);
    expect(severityRank("Low-Moderate")).toBe(0);
    expect(severityRank(null)).toBe(0);
    expect(severityRank("")).toBe(0);
  });

  it("inputSeverityRank casefolds and maps", () => {
    expect(inputSeverityRank("major")).toBe(3);
    expect(inputSeverityRank("HIGH")).toBe(3);
    expect(inputSeverityRank("Moderate")).toBe(2);
    expect(inputSeverityRank("low")).toBe(1);
    expect(inputSeverityRank(" unknown ")).toBe(0);
  });

  it("canonicalizeRegion handles aliases", () => {
    expect(canonicalizeRegion("USA")).toEqual(["us"]);
    expect(canonicalizeRegion("eu")).toEqual(["eu/eea"]);
    expect(canonicalizeRegion("India")).toEqual([
      "india",
      "india_expanded",
      "india_common_generic",
    ]);
    expect(canonicalizeRegion("")).toEqual([]);
    expect(canonicalizeRegion("mars")).toEqual(["mars"]);
  });
});
