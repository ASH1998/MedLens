import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { AGENT_SYSTEM_PROMPT, TOOL_LOOP_SYSTEM_PROMPT } from "../prompts";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..", "..");

describe("prompt parity with Python", () => {
  it("keeps browser prompt constants byte-for-byte identical to Python", () => {
    const pythonAgent = readPythonTripleQuotedConstant("medlens/agent.py", "AGENT_SYSTEM_PROMPT");
    const pythonToolSuffix = readPythonPromptSuffix("medlens/agent_loop.py");
    expect(AGENT_SYSTEM_PROMPT).toBe(pythonAgent);
    expect(TOOL_LOOP_SYSTEM_PROMPT).toBe(pythonAgent + pythonToolSuffix);
  });
});

function readPythonTripleQuotedConstant(relativePath: string, name: string): string {
  const source = fs.readFileSync(path.join(REPO_ROOT, relativePath), "utf8");
  const marker = `${name} = """`;
  const start = source.indexOf(marker);
  expect(start).toBeGreaterThanOrEqual(0);
  const valueStart = start + marker.length;
  const end = source.indexOf('"""', valueStart);
  expect(end).toBeGreaterThan(valueStart);
  return source.slice(valueStart, end);
}

function readPythonPromptSuffix(relativePath: string): string {
  const source = fs.readFileSync(path.join(REPO_ROOT, relativePath), "utf8");
  const marker = '+ """';
  const start = source.indexOf(marker);
  expect(start).toBeGreaterThanOrEqual(0);
  const valueStart = start + marker.length;
  const end = source.indexOf('"""', valueStart);
  expect(end).toBeGreaterThan(valueStart);
  return source.slice(valueStart, end);
}
