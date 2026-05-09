import { describe, expect, it } from "vitest";
import { normalizeLookupText } from "../normalize";

describe("normalizeLookupText", () => {
  it("casefolds and replaces non-alphanumerics", () => {
    expect(normalizeLookupText("Advil 200mg!")).toBe("advil 200mg");
    expect(normalizeLookupText("Aspirin/Low Dose")).toBe("aspirin low dose");
  });
  it("collapses whitespace and trims", () => {
    expect(normalizeLookupText("  para  cetamol   ")).toBe("para cetamol");
  });
  it("returns empty for empty / whitespace input", () => {
    expect(normalizeLookupText("")).toBe("");
    expect(normalizeLookupText("    ")).toBe("");
    expect(normalizeLookupText("---")).toBe("");
  });
});
